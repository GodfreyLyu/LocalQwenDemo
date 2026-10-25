"""Backend-owned prompts, validation and uncertain-execution recovery."""

import copy
import threading
import uuid

import pytest
from llm_contract.generated.ttypes import (
    GenerationOptions,
    GenerationResult,
    GenerationSnapshot,
    Health,
    Limits,
    LlmError,
    ModelInfo,
    TokenUsage,
)
from llm_contract.generated.ttypes import (
    GenerationState as State,
)
from pydantic import ValidationError

from app.errors import AppError, InferenceUncertain
from app.inference.rpc.client import ThriftReviewModel
from app.inference.settings import InferenceSettings
from backend.tests.support import register, wait_review

SOURCE = "def average(values):\n    return sum(values) / len(values)\n"
BODIES = [
    "The `average` function computes a mean from `values`.",
    "Empty `values` causes division by zero.",
    "Guard `average` against an empty list.",
]


class Remote:
    def __init__(self):
        self.info = ModelInfo(
            str(uuid.uuid4()),
            "qwen3:1.7b",
            "sha256:" + "a" * 64,
            GenerationOptions(128, 0.7, 0.8),
            Limits(1, 8, 128, 600000, 300000, 64000, 2048, 512),
            "Q4_K_M",
            "gpu",
        )
        self.requests, self.results = [], {}
        self.bodies = BODIES[:]
        self.error = None
        self.lost = False
        self.state = State.SUCCEEDED

    def call(self, method, *args):
        if method == "getModelInfo":
            return copy.deepcopy(self.info)
        if method == "getHealth":
            return Health(True, False, 0, 0)
        if method == "startGeneration":
            request = args[0]
            self.requests.append(request)
            result = GenerationResult(
                self.bodies[(len(self.requests) - 1) % 3],
                self.info.model_id,
                self.info.model_revision,
                TokenUsage(100, 20),
                "stop",
                20,
                5,
            )
            snapshot = GenerationSnapshot(
                request.request_id, self.info.instance_id, self.state, result, error=self.error
            )
            self.results[request.request_id] = snapshot
            if self.lost:
                self.lost = False
                raise OSError("PRIVATE_SOURCE")
            return snapshot
        if args[0] != self.info.instance_id:
            raise LlmError("instance_changed", "PRIVATE_SOURCE", False)
        return self.results[args[1]]


@pytest.fixture
def remote(monkeypatch):
    model = ThriftReviewModel(
        InferenceSettings(_env_file=None, thrift_host="127.0.0.1", thrift_recovery_seconds=0.1)
    )
    remote = Remote()
    monkeypatch.setattr(model, "_call", remote.call)
    model.load()
    return model, remote


@pytest.mark.component
@pytest.mark.contract
def test_backend_supplies_prompts_and_builds_the_review(remote):
    model, server = remote
    result = model.review(SOURCE, "python", threading.Event())
    assert result == "\n\n".join(
        f"## {title}\n{body}"
        for title, body in zip(("Summary", "Findings", "Suggestions"), BODIES, strict=True)
    )
    assert len(server.requests) == 3
    assert all(request.messages[0].role == "system" for request in server.requests)
    assert all(SOURCE in request.messages[1].content for request in server.requests)
    assert sum(r.options.max_output_tokens for r in server.requests) == 384
    assert all(r.options.temperature is None for r in server.requests)


@pytest.mark.component
@pytest.mark.recovery
def test_lost_acceptance_is_reconciled_by_id_without_resubmission(remote, caplog):
    model, server = remote
    server.lost = True
    assert model.review(SOURCE, "python", threading.Event()).startswith("## Summary")
    assert len(server.requests) == 3
    assert "PRIVATE" not in caplog.text


@pytest.mark.component
def test_fastapi_keeps_authentication_persistence_and_section_metrics(factory, remote):
    model, _ = remote
    client = factory(model)
    model.metrics = client.app.state.metrics
    register(client)
    response = client.post("/api/v1/reviews", json={"source_code": SOURCE, "language": "python"})
    result = wait_review(client, response.json()["review_id"])
    assert result["status"] == "completed" and result["model_revision"] == model.digest
    assert 'review_generated_tokens_total{section="summary"} 20.0' in client.get("/metrics").text


@pytest.mark.component
@pytest.mark.recovery
def test_unknown_execution_blocks_coordinator_without_repeating_generation(
    factory, remote, monkeypatch
):
    model, server = remote
    original = server.call

    def lost(method, *args):
        if method in {"getGeneration", "cancelGeneration"}:
            raise OSError("PRIVATE_SOURCE")
        return original(method, *args)

    server.lost = True
    monkeypatch.setattr(model, "_call", lost)
    client = factory(model)
    register(client)
    response = client.post("/api/v1/reviews", json={"source_code": SOURCE, "language": "python"})
    assert wait_review(client, response.json()["review_id"])["error_code"] == "inference_stuck"
    assert client.get("/health/live").status_code == 503
    assert len(server.requests) == 1


@pytest.mark.component
def test_worker_restart_is_not_retried(remote):
    model, server = remote
    server.info.instance_id = str(uuid.uuid4())
    with pytest.raises(InferenceUncertain):
        model.review(SOURCE, "python", threading.Event())


@pytest.mark.component
def test_output_validation_remains_in_backend(remote):
    model, server = remote
    server.bodies = ["Unrelated prose."] * 3
    with pytest.raises(AppError) as error:
        model.review(SOURCE, "python", threading.Event())
    assert error.value.code == "invalid_model_response"


@pytest.mark.unit
@pytest.mark.parametrize(
    "host", ["example.com", "169.254.169.254", "http://localhost", "127.0.0.1:9091"]
)
def test_thrift_endpoint_is_local(host):
    with pytest.raises(ValidationError):
        InferenceSettings(_env_file=None, thrift_host=host)


@pytest.mark.component
@pytest.mark.recovery
def test_cancellation_reconciliation_is_bounded_without_claiming_remote_exit(remote, monkeypatch):
    model, server = remote
    stop = threading.Event()
    original = server.call
    server.state = State.RUNNING

    def running(method, *args):
        snapshot = original(method, *args)
        if method == "startGeneration":
            stop.set()
        return snapshot

    monkeypatch.setattr(model, "_call", running)
    with pytest.raises(InferenceUncertain):
        model.review(SOURCE, "python", stop)
    assert len(server.requests) == 1
    assert server.results[server.requests[0].request_id].state == State.RUNNING


@pytest.mark.component
@pytest.mark.security
@pytest.mark.parametrize(
    "remote_code,public_code",
    [
        ("input_limit", "token_limit"),
        ("invalid_response", "invalid_model_response"),
        ("model_missing", "ollama_model_missing"),
        ("ollama_unavailable", "ollama_unavailable"),
    ],
)
def test_native_failures_keep_application_error_codes_without_remote_content(
    factory, remote, remote_code, public_code, capsys
):
    model, server = remote
    server.state = State.FAILED
    server.error = LlmError(remote_code, "PRIVATE_SOURCE_AND_GENERATED_CONTENT", False)
    client = factory(model)
    register(client)
    response = client.post("/api/v1/reviews", json={"source_code": SOURCE, "language": "python"})
    result = wait_review(client, response.json()["review_id"])
    assert result["status"] == "failed" and result["error_code"] == public_code
    assert "PRIVATE" not in str(result) + capsys.readouterr().err
