"""Native HTTP translation tests. These do not execute an Ollama model."""

import json
import threading

import httpx
import pytest
from llm_contract.generated.ttypes import GenerationOptions, GenerationRequest, Message
from llm_service.config import Settings
from llm_service.errors import ExecutionError
from llm_service.ollama import OllamaAdapter


class Process:
    def __init__(self):
        self.closing, self.recovering = threading.Event(), threading.Event()
        self.aborted = self.started = 0

    def alive(self):
        return True

    def abort(self):
        self.aborted += 1

    def start(self):
        self.started += 1


class NativeAPI:
    def __init__(self, settings):
        self.s = settings
        self.calls, self.payloads = [], []
        self.digest = settings.model_digest
        self.vram = 100
        self.installed = True
        self.terminal = {}

    def __call__(self, request):
        self.calls.append(request.url.path)
        model = {
            "name": self.s.model_name,
            "digest": self.digest,
            "size": 100,
            "size_vram": self.vram,
        }
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.24.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [model] if self.installed else []})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [model]})
        if request.url.path == "/api/pull":
            self.installed = True
            return httpx.Response(200, json={"status": "success"})
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion"],
                    "details": {"quantization_level": "Q4_K_M"},
                    "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 32768},
                },
            )
        assert request.url.path == "/api/chat"
        self.payloads.append(json.loads(request.content))
        terminal = {
            "model": self.s.model_name,
            "message": {"content": "Bonjour"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 8,
            "eval_count": 2,
            **self.terminal,
        }
        return httpx.Response(200, text=json.dumps(terminal) + "\n")


@pytest.fixture
def adapter(monkeypatch):
    settings = Settings(_env_file=None, model_wait_seconds=1)
    process = Process()
    model, api = OllamaAdapter(settings, process), NativeAPI(settings)
    monkeypatch.setattr(
        model,
        "_client",
        lambda: httpx.AsyncClient(
            base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(api)
        ),
    )
    return model, api, process


def test_generic_messages_options_identity_and_usage_are_preserved(adapter):
    model, api, process = adapter
    model.load()
    current = GenerationRequest(
        "abc",
        "instance",
        [Message("user", "Translate Hello to French")],
        GenerationOptions(16, 0.1, 0.9, 456),
        1000,
    )
    result = model.generate(current, threading.Event())
    assert result.content == "Bonjour" and result.usage.input_tokens == 8
    assert result.model_revision == model.settings.model_digest
    payload = api.payloads[-1]
    assert payload["messages"] == [{"role": "user", "content": "Translate Hello to French"}]
    assert payload["options"]["num_predict"] == 16
    assert payload["options"]["seed"] == 456
    assert payload["think"] is False and payload["truncate"] is False and payload["shift"] is False
    assert process.aborted == 0 and model.device == "gpu"


@pytest.mark.parametrize("vram", [0, 1, 99, None, True])
def test_cpu_partial_and_unverifiable_gpu_offload_fail(adapter, vram):
    model, api, _ = adapter
    api.vram = vram
    with pytest.raises(ExecutionError):
        model.load()
    assert not model.ready()


def test_pinned_model_is_never_replaced_on_mismatch(adapter):
    model, api, _ = adapter
    api.digest = "sha256:" + "b" * 64
    with pytest.raises(ExecutionError) as error:
        model.load()
    assert error.value.code == "model_mismatch"
    assert "/api/pull" not in api.calls and "/api/chat" not in api.calls


def test_pull_is_opt_in_and_restart_reuses_installed_model(adapter):
    model, api, _ = adapter
    api.installed = False
    with pytest.raises(ExecutionError):
        model.load()
    assert "/api/pull" not in api.calls
    model.settings.model_pull_if_missing = True
    model.load()
    model.load()
    assert api.calls.count("/api/pull") == 1


@pytest.mark.parametrize(
    "terminal,code",
    [
        ({"prompt_eval_count": 3000}, "input_limit"),
        ({"prompt_eval_count": True}, "invalid_response"),
        ({"eval_count": 9999}, "invalid_response"),
        ({"error": "PRIVATE_PROMPT"}, "invalid_response"),
        ({"error": "the input length exceeds the context length: PRIVATE_PROMPT"}, "input_limit"),
        ({"message": {"thinking": "PRIVATE_PROMPT"}}, "invalid_response"),
        ({"message": {"tool_calls": [{"name": "PRIVATE_PROMPT"}]}}, "invalid_response"),
        ({"done": False}, "invalid_response"),
        ({"done_reason": "unknown"}, "invalid_response"),
    ],
)
def test_native_failures_are_sanitized_and_native_process_is_reaped(adapter, terminal, code):
    model, api, process = adapter
    model.load()
    api.terminal = terminal
    # Recovery warmup may fail too; it must still fail closed.
    with pytest.raises(ExecutionError) as error:
        model.generate(GenerationRequest(messages=[Message("user", "hello")]), threading.Event())
    assert error.value.code == code
    assert "PRIVATE" not in error.value.message
    assert process.aborted == 1
