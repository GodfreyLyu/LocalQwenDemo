"""Transport, identity, admission and cancellation contracts; no external model needed."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import AppError
from app.inference.identity import model_identity
from app.inference.model import TransformersModel
from app.inference.ollama import OllamaModel, render_prompt
from app.main import create_app

SOURCE = "def average(values):\n    return sum(values) / len(values)\n"
DIGEST = "a" * 64
BODIES = [
    "The average function computes the mean of values.",
    "Empty values causes division by zero in average.",
    "Guard average against an empty values list before division.",
]


def settings(**kwargs):
    return Settings(
        _env_file=None,
        signing_secret="x" * 32,
        dynamodb_endpoint_url="http://localhost:8001",
        **kwargs,
    )


class Tokenizer:
    def encode(self, text, **kwargs):
        return SimpleNamespace(ids=list(range(200)))


class Server:
    def __init__(self):
        self.payloads = []
        self.status = 200
        self.override = {}
        self.missing = False
        self.digest = DIGEST
        self.truncated = False
        self.fail_connection = False

    async def __call__(self, request):
        if self.fail_connection:
            raise httpx.ConnectError("PRIVATE_URL", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": []
                    if self.missing
                    else [
                        {"name": "qwen3:1.7b", "digest": self.digest},
                    ]
                },
            )
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"digest": DIGEST, "size": 100, "size_vram": 100},
                    ]
                },
            )
        payload = json.loads(request.content)
        self.payloads.append(payload)
        index = (len(self.payloads) - 1) % 3
        item = {
            "model": "qwen3:1.7b",
            "message": {"content": BODIES[index]},
            "done": True,
            "prompt_eval_count": 200,
            "eval_count": 20,
            "done_reason": "stop",
        } | self.override
        if self.truncated:
            item["done"] = False
        return httpx.Response(self.status, text=json.dumps(item) + "\n")


@pytest.fixture
def adapter(monkeypatch):
    server = Server()
    model = OllamaModel(settings(model_backend="ollama", model_max_output_tokens=384))
    model.digest = "sha256:" + DIGEST
    model.tokenizer = Tokenizer()
    model.quantization = "Q4_K_M"
    monkeypatch.setattr(
        model,
        "_client",
        lambda: httpx.AsyncClient(
            base_url="http://localhost:11434",
            transport=httpx.MockTransport(server),
            trust_env=False,
        ),
    )
    return model, server


def test_real_three_section_contract_and_observed_identity(adapter):
    model, server = adapter
    result = model.review(SOURCE, "python", threading.Event())
    assert all(f"## {title}" in result for title in ("Summary", "Findings", "Suggestions"))
    assert all(body in result for body in BODIES)
    assert len(server.payloads) == 3
    assert sum(p["options"]["num_predict"] for p in server.payloads) == 384
    assert all(p["think"] is False and p["stream"] is True for p in server.payloads)
    assert all(p["options"]["num_ctx"] == 2432 for p in server.payloads)
    assert all(len(p["messages"]) == 2 for p in server.payloads)
    assert all(SOURCE in p["messages"][1]["content"] for p in server.payloads)
    identity = model_identity(model, model.settings)
    assert identity["model_revision"] == "sha256:" + DIGEST
    assert identity["model_id"] == "qwen3:1.7b"
    assert identity["quantization"] == "Q4_K_M" and identity["device"] == "gpu"
    assert identity["model_source"] == "ollama_api"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"status": 404}, "ollama_model_missing"),
        ({"status": 500}, "ollama_unavailable"),
        ({"missing": True}, "ollama_model_missing"),
        ({"fail_connection": True}, "ollama_unavailable"),
        ({"digest": "b" * 64}, "ollama_model_mismatch"),
        ({"override": {"prompt_eval_count": 199}}, "ollama_model_mismatch"),
        ({"override": {"eval_count": 999}}, "ollama_model_mismatch"),
        ({"override": {"message": {"thinking": "PRIVATE"}}}, "invalid_model_response"),
        ({"override": {"error": "PRIVATE"}}, "invalid_model_response"),
        ({"truncated": True}, "invalid_model_response"),
    ],
)
def test_errors_fail_closed_without_partial_review_or_fallback(adapter, change, code):
    model, server = adapter
    for name, value in change.items():
        setattr(server, name, value)
    with pytest.raises(AppError) as error:
        model.review(SOURCE, "python", threading.Event())
    assert error.value.code == code
    assert "PRIVATE" not in str(error.value)
    assert len(server.payloads) <= 1


def test_input_limit_checked_locally_before_generation(adapter):
    model, server = adapter
    model.settings.model_max_input_tokens = 128
    assert model.count_tokens(SOURCE, "python") == 200
    with pytest.raises(AppError, match="token limit"):
        model.review(SOURCE, "python", threading.Event())
    assert not server.payloads


@pytest.mark.parametrize("by_stop", [False, True])
def test_timeout_or_stop_cancels_inflight_stream_and_releases_client(adapter, monkeypatch, by_stop):
    model, server = adapter
    model.settings.inference_timeout_seconds = 0.1 if not by_stop else 5
    closed = threading.Event()
    stop = threading.Event()

    class HangingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                await asyncio.sleep(10)
                yield b""
            finally:
                closed.set()

    async def handler(request):
        if request.url.path != "/api/chat":
            return await server(request)
        if by_stop:
            stop.set()
        return httpx.Response(200, stream=HangingStream())

    monkeypatch.setattr(
        model,
        "_client",
        lambda: httpx.AsyncClient(
            base_url="http://localhost:11434",
            transport=httpx.MockTransport(handler),
        ),
    )
    start = time.monotonic()
    with pytest.raises(AppError) as error:
        model.review(SOURCE, "python", stop)
    assert error.value.code == "inference_timeout"
    assert closed.is_set() and time.monotonic() - start < 1


def test_coordinator_persists_digest_and_recovers_after_failed_request(
    adapter, factory, monkeypatch
):
    from uuid import uuid4

    from conftest import register, wait_review

    model, server = adapter
    monkeypatch.setattr(model, "load", lambda: None)
    client = factory(model)
    register(client)
    server.fail_connection = True

    def submit():
        response = client.post(
            "/api/v1/reviews",
            json={
                "source_code": SOURCE,
                "language": "python",
                "client_request_id": str(uuid4()),
            },
        )
        assert response.status_code == 202
        return wait_review(client, response.json()["review_id"])

    failed = submit()
    assert failed["error_code"] == "ollama_unavailable"
    server.fail_connection = False
    completed = submit()
    assert completed["status"] == "completed"
    assert completed["model_revision"] == "sha256:" + DIGEST
    assert completed["model_id"] == "qwen3:1.7b"
    assert client.get("/health/ready").status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:11434",
        "http://8.8.8.8:11434",
        "http://localhost:80",
        "http://user:pass@localhost:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?secret=value",
        "http://review-ollama.local-inference.svc.cluster.local.evil.com:11434",
        "http://review-ollama.local-inference.svc.cluster.local:11434/api",
        "http://review-ollama.local-inference.svc.cluster.local:8000",
    ],
)
def test_only_explicit_local_ollama_endpoints(url):
    with pytest.raises(ValidationError):
        settings(ollama_base_url=url)


def test_cluster_ollama_service_endpoint():
    url = "http://review-ollama.local-inference.svc.cluster.local:11434"
    assert settings(ollama_base_url=url).ollama_base_url == url


def test_factory_preserves_explicit_transformers_fallback():
    for backend, cls in [("transformers", TransformersModel), ("ollama", OllamaModel)]:
        app = create_app(settings(model_backend=backend), users=object())
        assert isinstance(app.state.model, cls)
    with pytest.raises(ValidationError):
        settings(model_backend="automatic")


def test_template_rendering_includes_think_control_and_generation_prefix():
    assert render_prompt(
        [{"role": "system", "content": "rules"}, {"role": "user", "content": "code"}]
    ) == (
        "<|im_start|>system\nrules<|im_end|>\n<|im_start|>user\ncode /no_think<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def test_tokenizer_validation_rejects_changed_template_vocabulary_and_merges():
    from copy import deepcopy
    from pathlib import Path

    from app.inference.ollama import validate_tokenizer

    document = {
        "model": {"vocab": {"a": 0}, "merges": [["a", "b"]]},
        "added_tokens": [{"id": 1, "content": "<special>"}],
    }
    metadata = {
        "template": (Path(__file__).parent / "fixtures/ollama-qwen3-template.txt").read_text(),
        "model_info": {
            "general.architecture": "qwen3",
            "general.size_label": "1.7B",
            "tokenizer.ggml.pre": "qwen2",
            "tokenizer.ggml.add_bos_token": False,
            "tokenizer.ggml.eos_token_id": 151645,
            "tokenizer.ggml.tokens": ["a", "<special>"],
            "tokenizer.ggml.merges": ["a b"],
        },
    }
    validate_tokenizer(document, metadata)
    for key, value in [
        ("tokenizer.ggml.tokens", ["b", "<special>"]),
        ("tokenizer.ggml.merges", ["b a"]),
        ("tokenizer.ggml.add_bos_token", True),
        ("general.size_label", "0.6B"),
    ]:
        changed = deepcopy(metadata)
        changed["model_info"][key] = value
        with pytest.raises(AppError):
            validate_tokenizer(document, changed)
    with pytest.raises(AppError):
        validate_tokenizer(document, metadata | {"template": "different"})
    with pytest.raises(AppError):
        validate_tokenizer(document, metadata | {"system": "custom instructions"})


def test_startup_retries_transient_network_but_has_a_fixed_deadline(adapter, monkeypatch):
    model, _ = adapter
    elapsed = [0.0]
    calls = []
    monkeypatch.setattr("app.inference.ollama.time.monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        "app.inference.ollama.time.sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay)
    )

    def unavailable(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise AppError("ollama_unavailable", "Service unavailable.", 503)

    monkeypatch.setattr(model, "_run", unavailable)
    with pytest.raises(AppError):
        model._startup_metadata(None)
    assert elapsed[0] == 60
    assert 1 < len(calls) < 20 and max(calls) <= 15


@pytest.mark.parametrize(
    "code", ["ollama_model_missing", "ollama_model_mismatch", "invalid_model_response"]
)
def test_startup_never_retries_configuration_or_protocol_errors(adapter, monkeypatch, code):
    model, _ = adapter
    calls = []

    def invalid(*args, **kwargs):
        calls.append(1)
        raise AppError(code, "Configuration rejected.", 503)

    monkeypatch.setattr(model, "_run", invalid)
    with pytest.raises(AppError) as error:
        model._startup_metadata(None)
    assert error.value.code == code and len(calls) == 1


def test_startup_recovers_from_one_connection_failure(adapter, monkeypatch):
    model, _ = adapter
    calls = []

    def transient(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise AppError("ollama_unavailable", "Service unavailable.", 503)
        return {"model_info": {}}

    monkeypatch.setattr(model, "_run", transient)
    monkeypatch.setattr("app.inference.ollama.time.sleep", lambda _: None)
    assert model._startup_metadata(None) == {"model_info": {}}
    assert len(calls) == 2


def test_default_backend_calls_cluster_ollama():
    config = settings()
    assert config.model_backend == "ollama"
    assert config.ollama_base_url == "http://review-ollama.local-inference.svc.cluster.local:11434"
    app = create_app(config, users=object())
    assert isinstance(app.state.model, OllamaModel)
