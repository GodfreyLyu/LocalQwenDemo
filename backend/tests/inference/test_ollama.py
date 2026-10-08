"""Transport, identity, admission and cancellation contracts; no external model needed."""

import asyncio
import json
import threading
import time

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import AppError
from app.inference.identity import model_identity
from app.inference.ollama import OllamaModel
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


class Server:
    def __init__(self):
        self.payloads = []
        self.name = "qwen3:1.7b"
        self.version = "0.24.0"
        self.metadata = {
            "capabilities": ["completion"],
            "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 32768},
            "details": {"quantization_level": "Q4_K_M"},
        }
        self.status = 200
        self.override = {}
        self.missing = False
        self.digest = DIGEST
        self.truncated = False
        self.fail_connection = False

    async def __call__(self, request):
        if self.fail_connection:
            raise httpx.ConnectError("PRIVATE_URL", request=request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": self.version})
        if request.url.path == "/api/show":
            return httpx.Response(200, json=self.metadata)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": []
                    if self.missing
                    else [
                        {"name": self.name, "digest": self.digest},
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
            "model": self.name,
            "message": {"content": BODIES[index]},
            "done": True,
            "prompt_eval_count": 200,
            "eval_count": min(20, payload["options"]["num_predict"]),
            "done_reason": "stop",
        } | self.override
        if self.truncated:
            item["done"] = False
        return httpx.Response(self.status, text=json.dumps(item) + "\n")


@pytest.fixture
def adapter(monkeypatch):
    server = Server()
    model = OllamaModel(settings(model_max_output_tokens=384))
    model.digest = "sha256:" + DIGEST
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


@pytest.mark.component
@pytest.mark.contract
def test_real_three_section_contract_and_observed_identity(adapter):
    model, server = adapter
    result = model.review(SOURCE, "python", threading.Event())
    assert all(f"## {title}" in result for title in ("Summary", "Findings", "Suggestions"))
    assert all(body in result for body in BODIES)
    assert len(server.payloads) == 3
    assert sum(p["options"]["num_predict"] for p in server.payloads) == 384
    assert all(p["think"] is False and p["stream"] is True for p in server.payloads)
    assert all(p["options"]["num_ctx"] == 4096 for p in server.payloads)
    assert all(len(p["messages"]) == 2 for p in server.payloads)
    assert all(SOURCE in p["messages"][1]["content"] for p in server.payloads)
    identity = model_identity(model)
    assert identity["model_revision"] == "sha256:" + DIGEST
    assert identity["model_id"] == "qwen3:1.7b"
    assert identity["quantization"] == "Q4_K_M" and identity["device"] == "gpu"
    assert identity["model_source"] == "ollama_api"


@pytest.mark.component
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"status": 404}, "ollama_model_missing"),
        ({"status": 500}, "ollama_unavailable"),
        ({"missing": True}, "ollama_model_missing"),
        ({"fail_connection": True}, "ollama_unavailable"),
        ({"digest": "b" * 64}, "ollama_model_mismatch"),
        ({"override": {"prompt_eval_count": 0}}, "ollama_model_mismatch"),
        ({"override": {"prompt_eval_count": True}}, "ollama_model_mismatch"),
        ({"override": {"prompt_eval_count": 2049}}, "token_limit"),
        ({"override": {"error": "the input length exceeds the context length"}}, "token_limit"),
        (
            {"status": 400, "override": {"error": "the input length exceeds the context length"}},
            "token_limit",
        ),
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


@pytest.mark.component
def test_actual_input_limit_is_checked_by_worker_before_result_is_published(adapter):
    model, server = adapter
    model.settings.model_max_input_tokens = 128
    with pytest.raises(AppError, match="token limit"):
        model.review(SOURCE, "python", threading.Event())
    assert len(server.payloads) == 1


@pytest.mark.component
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


@pytest.mark.component
@pytest.mark.contract
@pytest.mark.recovery
def test_coordinator_persists_digest_and_recovers_after_failed_request(
    adapter, factory, monkeypatch
):
    from uuid import uuid4

    from backend.tests.support import register, wait_review

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


@pytest.mark.component
@pytest.mark.security
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


@pytest.mark.component
@pytest.mark.security
def test_cluster_ollama_service_endpoint():
    url = "http://review-ollama.local-inference.svc.cluster.local:11434"
    assert settings(ollama_base_url=url).ollama_base_url == url


@pytest.mark.component
def test_factory_always_uses_ollama():
    app = create_app(settings(), users=object())
    assert isinstance(app.state.model, OllamaModel)


@pytest.mark.component
def test_load_and_review_a_different_model_without_local_tokenizer(adapter):
    model, server = adapter
    server.name = model.settings.ollama_model = "different-family:small"
    server.metadata["model_info"] = {"general.architecture": "llama", "llama.context_length": 8192}
    model.settings.model_temperature = 0.2
    model.settings.model_top_k = 40
    model.settings.model_keep_alive = "30s"
    model.load()
    server.payloads.clear()
    assert "## Findings" in model.review(SOURCE, "python", threading.Event())
    assert all(p["model"] == server.name and p["keep_alive"] == "30s" for p in server.payloads)
    assert all(p["truncate"] is False and p["shift"] is False for p in server.payloads)
    assert all(
        p["options"]["temperature"] == 0.2 and p["options"]["top_k"] == 40 for p in server.payloads
    )
    assert not hasattr(model, "tokenizer")


@pytest.mark.component
@pytest.mark.parametrize("version", ["0.23.9", "0.9.0", "unknown", ""])
def test_old_or_unknown_ollama_cannot_silently_ignore_context_protection(adapter, version):
    model, server = adapter
    server.version = version
    with pytest.raises(AppError) as error:
        model.load()
    assert error.value.code == "ollama_version_unsupported"
    assert not server.payloads


@pytest.mark.component
@pytest.mark.parametrize(
    "metadata",
    [
        {"capabilities": ["embedding"]},
        {"model_info": {"general.architecture": "llama", "llama.context_length": 2048}},
        {"model_info": {}},
        {"remote_host": "https://private.invalid"},
    ],
)
def test_incompatible_model_fails_before_generation(adapter, metadata):
    model, server = adapter
    server.metadata.update(metadata)
    with pytest.raises(AppError, match="configuration"):
        model.load()
    assert not server.payloads


@pytest.mark.component
@pytest.mark.contract
def test_configuration_is_snapshotted_for_the_model_instance():
    config = settings()
    model = OllamaModel(config)
    config.ollama_model = "changed:latest"
    assert model.settings.ollama_model == "qwen3:1.7b"
    assert model_identity(model)["model_id"] == "qwen3:1.7b"


@pytest.mark.component
@pytest.mark.recovery
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


@pytest.mark.component
@pytest.mark.contract
@pytest.mark.recovery
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


@pytest.mark.component
@pytest.mark.recovery
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


@pytest.mark.component
def test_default_backend_calls_cluster_ollama():
    config = settings()
    assert config.ollama_base_url == "http://review-ollama.local-inference.svc.cluster.local:11434"
    app = create_app(config, users=object())
    assert isinstance(app.state.model, OllamaModel)


@pytest.mark.component
@pytest.mark.contract
def test_review_uses_shared_sampling_and_digest_seed_for_each_section(adapter):
    from app.inference.generation import derive_generation_seed
    from app.inference.prompts import SECTION_SPECS

    model, server = adapter
    model.review(SOURCE, "python", threading.Event())
    first = [p["options"] for p in server.payloads]
    for (section, _, _), options in zip(SECTION_SPECS, first, strict=True):
        assert options.items() >= model.settings.generation_parameters.items()
        assert options["seed"] == derive_generation_seed(model.digest, "python", section, SOURCE)
    model.review(SOURCE, "python", threading.Event())
    assert first == [p["options"] for p in server.payloads[3:]]
    assert len({p["seed"] for p in first}) == 3


@pytest.mark.component
@pytest.mark.parametrize("body", ["", "## Findings\nUnexpected section."])
def test_invalid_section_stops_later_requests(adapter, body):
    model, server = adapter
    server.override = {"message": {"content": body}}
    with pytest.raises(AppError) as error:
        model.review(SOURCE, "python", threading.Event())
    assert error.value.code == "invalid_model_response"
    assert len(server.payloads) == 1


@pytest.mark.component
def test_capped_tail_is_trimmed_and_metrics_never_include_content(adapter, caplog):
    model, server = adapter
    server.override = {
        "message": {
            "content": "The average function needs an empty values guard. PRIVATE_FRAGMENT"
        },
        "done_reason": "length",
    }
    with caplog.at_level("INFO", logger="review"):
        result = model.review(SOURCE, "python", threading.Event())
    assert "PRIVATE_FRAGMENT" not in result
    events = [r for r in caplog.records if r.msg == "model_generation_finished"]
    assert len(events) == 1
    assert all(events[0].section_trailing_fragments_removed.values())
    from app.logging import SafeFormatter

    assert "PRIVATE_FRAGMENT" not in SafeFormatter().format(events[0])


@pytest.mark.component
@pytest.mark.parametrize("stage", ["ollama_validation", "startup_generation", "post_model_storage"])
def test_startup_diagnostics_never_format_exception(stage, caplog):
    from app.logging import SafeFormatter
    from app.startup import startup_stage

    with pytest.raises(ValueError), startup_stage(stage):
        raise ValueError("PRIVATE_SOURCE_PROMPT_MODEL_TOKEN_SENTINEL")
    payload = json.loads(SafeFormatter().format(caplog.records[-1]))
    assert payload["stage"] == stage
    assert payload["exception_type"] == "ValueError"
    assert "SENTINEL" not in json.dumps(payload)


@pytest.mark.component
@pytest.mark.parametrize(
    "override",
    [
        {"prompt_eval_count": 129},
        {"error": "the input length exceeds the context length"},
    ],
)
def test_admitted_review_fails_safely_when_ollama_rejects_input_tokens(
    adapter, factory, monkeypatch, override
):
    from backend.tests.support import register, wait_review

    model, server = adapter
    model.settings.model_max_input_tokens = 128
    server.override = override
    monkeypatch.setattr(model, "load", lambda: None)
    client = factory(model, model_max_input_tokens=128)
    register(client)
    response = client.post("/api/v1/reviews", json={"source_code": SOURCE, "language": "python"})
    assert response.status_code == 202
    failed = wait_review(client, response.json()["review_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "token_limit"
    assert failed["review_result"] is None
    assert len(server.payloads) == 1
