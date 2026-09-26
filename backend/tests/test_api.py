import json
import logging
import re
import sqlite3
import threading
import time
from uuid import uuid4

import boto3
import pytest
from argon2 import extract_parameters
from argon2.low_level import Type
from conftest import FakeModel, register, wait_ready, wait_review
from fastapi.testclient import TestClient

from app.coordinator import milliseconds_since
from app.errors import AppError, ValidationReason
from app.inference.model import INVALID_REVIEW_MESSAGE, validate_review_output
from app.logging import SERVICE_NAME, SafeFormatter


def test_registration_hashing_cookie_and_normalization(factory):
    client = factory()
    response = register(client, " Alice ")
    assert response.json()["login_id"] == "alice"
    assert all(
        flag in response.headers["set-cookie"]
        for flag in ["__Host-review_session", "HttpOnly", "Secure", "SameSite=strict"]
    )
    user = (
        boto3.resource("dynamodb", region_name="us-east-1")
        .Table("test-users")
        .get_item(Key={"login_id": "alice"})["Item"]
    )
    assert extract_parameters(user["password_hash"]).type is Type.ID
    assert "correct-horse" not in user["password_hash"]
    assert (
        client.post(
            "/api/v1/auth/register", json={"login_id": "ALICE", "password": "correct-horse-battery"}
        ).status_code
        == 409
    )
    assert client.get("/api/v1/auth/me").json()["login_id"] == "alice"


def test_logout_revokes_replayed_cookie_and_login(factory):
    client = factory()
    register(client)
    old = client.cookies.get("__Host-review_session")
    assert client.post("/api/v1/auth/logout", json={}).status_code == 204
    client.cookies.set("__Host-review_session", old)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/auth/login", json={"login_id": "alice", "password": "a-wrong-password"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"login_id": "alice", "password": "correct-horse-battery"}
        ).status_code
        == 200
    )


def test_session_expiry_and_disabled_user(factory):
    client = factory()
    register(client)
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-users")
    for disabled, expected in [(True, 401), (False, 200)]:
        table.update_item(
            Key={"login_id": "alice"},
            UpdateExpression="SET disabled = :v",
            ExpressionAttributeValues={":v": disabled},
        )
        assert client.get("/api/v1/auth/me").status_code == expected
    with client.app.state.store.connection(write=True) as db:
        db.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_and_json_only(factory):
    client = factory()
    payload = {"login_id": "alice", "password": "correct-horse-battery"}
    assert (
        client.post(
            "/api/v1/auth/register", json=payload, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    register(client)
    for path, body in [("/api/v1/reviews", {"source_code": "x=1"}), ("/api/v1/auth/logout", {})]:
        assert client.post(path, json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
        assert (
            client.post(path, json=body, headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
    assert (
        client.post(
            "/api/v1/auth/login", content="{}", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )


def test_user_isolation_and_model_provenance(factory):
    model = FakeModel()
    client = factory(model)
    register(client)
    response = client.post("/api/v1/reviews", json={"source_code": "def f(x): return 1/x"})
    assert response.status_code == 202
    review_id = response.json()["review_id"]
    review = wait_review(client, review_id)
    assert review["status"] == "completed"
    assert review["model_id"] == "Simulated model"
    assert review["model_revision"] == "fixture-v1" and "user_id" not in review
    other = TestClient(
        client.app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    )
    register(other, "bob")
    assert other.get(f"/api/v1/reviews/{review_id}").status_code == 404
    assert other.get("/api/v1/reviews").json()["items"] == []
    assert other.get(f"/api/v1/reviews?before={review_id}").status_code == 400
    assert client.get("/api/v1/reviews").json()["items"][0]["review_id"] == review_id
    assert model.peak == 1


def test_idempotency_conflicts_and_completed_retry(factory):
    model = FakeModel(delay=0.1)
    client = factory(model)
    register(client)
    payload = {"source_code": "hello arbitrary source", "client_request_id": str(uuid4())}
    first = client.post("/api/v1/reviews", json=payload)
    again = client.post("/api/v1/reviews", json=payload)
    assert first.status_code == again.status_code == 202
    assert first.json()["review_id"] == again.json()["review_id"]
    assert (
        client.post("/api/v1/reviews", json=payload | {"source_code": "different"}).status_code
        == 409
    )
    wait_review(client, first.json()["review_id"])
    assert client.post("/api/v1/reviews", json=payload).json()["status"] == "completed"
    assert model.calls == 1


@pytest.mark.parametrize(
    "code,error",
    [("", "validation_error"), (" \n\t", "empty_input"), ("x" * 121, "input_too_large")],
)
def test_input_limits(factory, code, error):
    client = factory(source_max_chars=120)
    register(client)
    response = client.post("/api/v1/reviews", json={"source_code": code})
    assert response.status_code == 422 and response.json()["error"]["code"] == error


def test_tokens_invalid_id_body_limit_and_no_sensitive_echo(factory, capsys):
    client = factory(model_max_input_tokens=128)
    register(client)
    assert (
        client.post("/api/v1/reviews", json={"source_code": "word " * 100}).json()["error"]["code"]
        == "token_limit"
    )
    assert client.get("/api/v1/reviews/not-a-uuid").status_code == 422
    assert client.get(f"/api/v1/reviews/{uuid4()}").status_code == 404
    secret = "sensitive-password-or-source"
    response = client.post("/api/v1/reviews", json={"source_code": {"secret": secret}})
    assert secret not in response.text
    assert client.post("/api/v1/reviews", json={"source_code": "x" * 140000}).status_code == 413
    assert secret not in capsys.readouterr().err
    assert response.headers["x-request-id"] and response.headers["cache-control"] == "no-store"


def test_submission_and_login_rate_limits(factory):
    client = factory(
        FakeModel(delay=0.4), queue_capacity=1, submission_rate_limit=2, login_rate_limit=2
    )
    register(client)
    assert client.post("/api/v1/reviews", json={"source_code": "x"}).status_code == 202
    assert client.post("/api/v1/reviews", json={"source_code": "y"}).status_code in (409, 429)
    assert client.post("/api/v1/reviews", json={"source_code": "z"}).status_code == 429
    payload = {"login_id": "unknown", "password": "a-wrong-password"}
    assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    limited = client.post("/api/v1/auth/login", json=payload)
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    "result,code",
    [
        ("", "empty_model_response"),
        (RuntimeError("secret details"), "inference_failed"),
        (
            AppError(
                "invalid_model_response",
                "The model returned an unusable review. Try a smaller, self-contained snippet.",
                502,
            ),
            "invalid_model_response",
        ),
    ],
)
def test_model_failure_translation(factory, result, code):
    client = factory(FakeModel(result=result))
    register(client)
    job = client.post("/api/v1/reviews", json={"source_code": "x"}).json()
    review = wait_review(client, job["review_id"])
    assert review["status"] == "failed" and review["error_code"] == code
    assert "secret details" not in str(review)


def test_invalid_model_response_public_contract_and_safe_validation_log(factory, capsys):
    class ValidatingModel(FakeModel):
        def review(self, source, language, stop):
            return validate_review_output(self.result, source)

    source = "def private_source_identifier(private_values): return private_values"
    rejected_output = (
        "## Summary\nMODEL_OUTPUT_SENTINEL generic routine.\n"
        "## Findings\nNo issue.\n## Suggestions\nAdd tests."
    )
    client = factory(ValidatingModel(result=rejected_output))
    register(client)
    capsys.readouterr()

    job = client.post("/api/v1/reviews", json={"source_code": source}).json()
    review = wait_review(client, job["review_id"])
    log_lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    finished = [event for event in log_lines if event["event"] == "review_finished"]

    assert review["status"] == "failed"
    assert review["error_code"] == "invalid_model_response"
    assert review["error_message"] == INVALID_REVIEW_MESSAGE
    assert review["review_result"] is None
    assert "validation_reason" not in review
    assert len(finished) == 1
    assert {
        "level": "INFO",
        "service": SERVICE_NAME,
        "environment": "test",
        "event": "review_finished",
        "review_id": job["review_id"],
        "error_code": "invalid_model_response",
        "outcome": "invalid_model_response",
        "validation_reason": "detached_source",
    }.items() <= finished[0].items()
    assert finished[0]["duration_ms"] >= 0
    serialized_logs = json.dumps(log_lines)
    assert "private_source_identifier" not in serialized_logs
    assert "private_values" not in serialized_logs
    assert "MODEL_OUTPUT_SENTINEL" not in serialized_logs


def test_truncated_section_keeps_public_error_generic_and_logs_only_fixed_reason(factory, capsys):
    source_sentinel = "PRIVATE_TRUNCATED_SOURCE_SENTINEL"
    model_output_sentinel = "PRIVATE_TRUNCATED_MODEL_OUTPUT_SENTINEL"
    error = AppError(
        "invalid_model_response",
        INVALID_REVIEW_MESSAGE,
        502,
        validation_reason=ValidationReason.TRUNCATED_SECTION,
    )
    client = factory(FakeModel(result=error))
    register(client)
    capsys.readouterr()

    job = client.post("/api/v1/reviews", json={"source_code": source_sentinel}).json()
    review = wait_review(client, job["review_id"])
    log_lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    finished = [event for event in log_lines if event["event"] == "review_finished"]

    assert review["status"] == "failed"
    assert review["error_code"] == "invalid_model_response"
    assert review["error_message"] == INVALID_REVIEW_MESSAGE
    assert review["review_result"] is None
    assert "validation_reason" not in review
    assert len(finished) == 1
    assert {
        "level": "INFO",
        "service": SERVICE_NAME,
        "environment": "test",
        "event": "review_finished",
        "review_id": job["review_id"],
        "error_code": "invalid_model_response",
        "outcome": "invalid_model_response",
        "validation_reason": "truncated_section",
    }.items() <= finished[0].items()
    assert finished[0]["duration_ms"] >= 0
    serialized_logs = json.dumps(log_lines)
    assert source_sentinel not in serialized_logs
    assert model_output_sentinel not in serialized_logs


def test_generation_metric_formatter_keeps_only_safe_fixed_fields():
    record = logging.LogRecord(
        "review",
        logging.INFO,
        __file__,
        1,
        "model_generation_finished",
        (),
        None,
    )
    record.duration_ms = 123
    record.generated_tokens = 24
    diagnostics = {
        "section_prepare_ms": {"summary": 2, "findings": 3, "suggestions": None},
        "section_generation_ms": {"summary": 50, "findings": 73, "suggestions": None},
        "section_first_token_ms": {"summary": 10, "findings": 20, "suggestions": None},
        "section_input_tokens": {"summary": 40, "findings": 45, "suggestions": None},
        "worker_intraop_threads": 2,
        "worker_interop_threads": 10,
    }
    for key, value in diagnostics.items():
        setattr(record, key, value)
    record.output_limit_reached = False
    record.output_token_limit = 384
    record.section_generated_tokens = {"summary": 7, "findings": 8, "suggestions": 9}
    record.section_limits_reached = {
        "summary": False,
        "findings": False,
        "suggestions": False,
    }
    record.section_trailing_fragments_removed = {
        "summary": True,
        "findings": False,
        "suggestions": True,
    }
    record.source = "PRIVATE_SOURCE_SENTINEL"
    record.model_output = "MODEL_OUTPUT_SENTINEL"
    record.token_ids = [101, 202, 303]
    record.generation_seed = 42
    record.source_hash = "SOURCE_HASH_SENTINEL"

    payload = json.loads(
        SafeFormatter(environment="local", release_sha="abcdef012345").format(record)
    )

    timestamp = payload.pop("timestamp")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", timestamp)
    assert payload == {
        "level": "INFO",
        "service": "review-backend",
        "environment": "local",
        "release_sha": "abcdef012345",
        "event": "model_generation_finished",
        "duration_ms": 123,
        **diagnostics,
        "generated_tokens": 24,
        "output_limit_reached": False,
        "output_token_limit": 384,
        "section_generated_tokens": {"summary": 7, "findings": 8, "suggestions": 9},
        "section_limits_reached": {
            "summary": False,
            "findings": False,
            "suggestions": False,
        },
        "section_trailing_fragments_removed": {
            "summary": True,
            "findings": False,
            "suggestions": True,
        },
    }
    assert "PRIVATE_SOURCE_SENTINEL" not in json.dumps(payload)
    assert "MODEL_OUTPUT_SENTINEL" not in json.dumps(payload)
    assert "101" not in json.dumps(payload)
    assert "generation_seed" not in payload
    assert "source_hash" not in payload


def test_formatter_drops_unknown_validation_reason():
    record = logging.LogRecord("review", logging.INFO, __file__, 1, "review_finished", (), None)
    record.validation_reason = "PRIVATE_UNBOUNDED_REASON"

    payload = json.loads(SafeFormatter(environment="test").format(record))

    assert "validation_reason" not in payload


def test_http_log_uses_monotonic_duration_and_route_template(factory, monkeypatch, capsys):
    client = factory()
    register(client)
    capsys.readouterr()
    ticks = iter([100.0, 100.125])
    monkeypatch.setattr("app.api.middleware.monotonic", lambda: next(ticks))
    real_review_id = str(uuid4())

    response = client.get(f"/api/v1/reviews/{real_review_id}?secret=PRIVATE_QUERY")

    assert response.status_code == 404
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    request_event = next(event for event in events if event["event"] == "http_request")
    assert request_event["method"] == "GET"
    assert request_event["route"] == "/api/v1/reviews/{review_id}"
    assert request_event["duration_ms"] == 125
    assert request_event["error_code"] == "review_not_found"
    serialized = json.dumps(request_event)
    assert real_review_id not in serialized
    assert "PRIVATE_QUERY" not in serialized


def test_successful_health_probes_are_suppressed_but_failures_are_logged(factory, capsys):
    client = factory()
    capsys.readouterr()

    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert "http_request" not in capsys.readouterr().err

    release = threading.Event()
    entered = threading.Event()

    class LoadingModel(FakeModel):
        def load(self):
            entered.set()
            assert release.wait(5)

    loading = factory(LoadingModel(), ready=False)
    try:
        # Account-startup logs must finish before asserting the last HTTP event.
        assert entered.wait(2)
        capsys.readouterr()
        assert loading.get("/health/ready").status_code == 503
        events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    finally:
        release.set()
        wait_ready(loading)
    assert [event["route"] for event in events if event["event"] == "http_request"] == [
        "/health/ready"
    ]
    assert events[-1]["error_code"] == "model_loading"


def test_review_lifecycle_logs_safe_queue_metrics(factory, capsys):
    client = factory()
    register(client)
    capsys.readouterr()

    submitted = client.post(
        "/api/v1/reviews", json={"source_code": "PRIVATE_SOURCE_SENTINEL"}
    ).json()
    wait_review(client, submitted["review_id"])
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    lifecycle = [
        event
        for event in events
        if event["event"] in {"review_submitted", "review_started", "review_finished"}
    ]

    assert [event["event"] for event in lifecycle] == [
        "review_submitted",
        "review_started",
        "review_finished",
    ]
    assert lifecycle[0]["queue_depth"] == 1
    assert lifecycle[1]["queue_depth"] == 0
    assert lifecycle[1]["queue_wait_ms"] >= 0
    assert lifecycle[2]["duration_ms"] >= lifecycle[1]["queue_wait_ms"]
    assert "PRIVATE_SOURCE_SENTINEL" not in json.dumps(events)


def test_queue_rejection_log_has_only_bounded_fields(factory, monkeypatch, capsys):
    client = factory()
    register(client)
    capsys.readouterr()

    def reject(*args, **kwargs):
        raise AppError("queue_full", "The review queue is full. Try again later.", 429)

    monkeypatch.setattr(client.app.state.store, "create_review", reject)
    monkeypatch.setattr(client.app.state.store, "queue_depth", lambda: 8)
    response = client.post("/api/v1/reviews", json={"source_code": "PRIVATE_REJECTED_SOURCE"})

    assert response.status_code == 429
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    rejected = next(event for event in events if event["event"] == "queue_rejected")
    assert rejected["queue_depth"] == 8
    assert rejected["outcome"] == "rejected"
    assert rejected["error_code"] == "queue_full"
    assert "PRIVATE_REJECTED_SOURCE" not in json.dumps(events)


def test_queue_metric_failure_does_not_change_review_or_rejection_behavior(
    factory, monkeypatch, capsys
):
    client = factory()
    register(client)

    def broken_depth():
        raise sqlite3.OperationalError("PRIVATE_DATABASE_PATH")

    monkeypatch.setattr(client.app.state.store, "queue_depth", broken_depth)
    completed = client.post("/api/v1/reviews", json={"source_code": "x = 1"}).json()
    assert wait_review(client, completed["review_id"])["status"] == "completed"

    def reject(*args, **kwargs):
        raise AppError("queue_full", "The review queue is full. Try again later.", 429)

    monkeypatch.setattr(client.app.state.store, "create_review", reject)
    response = client.post("/api/v1/reviews", json={"source_code": "y = 2"})
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]

    assert response.status_code == 429
    rejected = next(event for event in events if event["event"] == "queue_rejected")
    assert "queue_depth" not in rejected
    assert "PRIVATE_DATABASE_PATH" not in json.dumps(events)


def test_persisted_queue_wait_calculation_is_clamped_and_exact():
    assert milliseconds_since(100.0, now=100.125) == 125
    assert milliseconds_since(101.0, now=100.0) == 0


def test_inference_timeout(factory):
    model = FakeModel(delay=1)
    client = factory(model, inference_timeout_seconds=0.03)
    register(client)
    job = client.post("/api/v1/reviews", json={"source_code": "x"}).json()
    assert wait_review(client, job["review_id"])["error_code"] == "inference_timeout"
    assert model.peak == 1 and client.get("/health/live").status_code == 200


def test_history_read_and_save_failure(factory, monkeypatch):
    client = factory()
    register(client)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("sensitive filesystem path")

    monkeypatch.setattr(client.app.state.store, "history", broken)
    response = client.get("/api/v1/reviews")
    assert response.status_code == 503 and "sensitive" not in response.text
    monkeypatch.setattr(client.app.state.store, "create_review", broken)
    assert client.post("/api/v1/reviews", json={"source_code": "x"}).status_code == 503


def test_readiness_while_model_loading(factory):
    class Loading(FakeModel):
        def load(self):
            time.sleep(0.2)

    client = factory(Loading(), ready=False)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_unexpected_errors_do_not_escape_to_transport_logs(factory, monkeypatch, capsys):
    client = factory()
    register(client)

    def broken(*args):
        raise RuntimeError("source-and-password-must-not-escape")

    monkeypatch.setattr(client.app.state.model, "count_tokens", broken)
    response = client.post("/api/v1/reviews", json={"source_code": "private source"})
    assert response.status_code == 500
    assert "source-and-password" not in response.text + capsys.readouterr().err


def test_serial_inference_across_users_keeps_health_responsive(factory):
    model = FakeModel(delay=0.3)
    alice = factory(model)
    register(alice)
    bob = TestClient(
        alice.app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    )
    register(bob, "bob")
    a = alice.post("/api/v1/reviews", json={"source_code": "a"}).json()
    b = bob.post("/api/v1/reviews", json={"source_code": "b"}).json()
    assert bob.get("/health/live").status_code == 200
    assert wait_review(alice, a["review_id"])["status"] == "completed"
    assert wait_review(bob, b["review_id"])["status"] == "completed"
    assert model.calls == 2 and model.peak == 1


def test_stuck_native_inference_fails_liveness_without_starting_a_second_job(factory):
    class Uncooperative(FakeModel):
        def review(self, source, language, stop):
            self.calls += 1
            time.sleep(0.4)
            return "late result"

    model = Uncooperative()
    client = factory(model, inference_timeout_seconds=0.02, inference_drain_seconds=0.02)
    register(client)
    job = client.post("/api/v1/reviews", json={"source_code": "x"}).json()
    assert wait_review(client, job["review_id"])["error_code"] == "inference_timeout"
    time.sleep(0.05)
    assert client.get("/health/live").status_code == 503
    assert client.post("/api/v1/reviews", json={"source_code": "next"}).status_code == 503
    assert model.calls == 1


def test_cache_failure_keeps_public_readiness_contract(factory):
    from app.inference.model_cache import ModelCacheIncompleteError

    class IncompleteCacheModel:
        def load(self):
            raise ModelCacheIncompleteError("missing_shard")

    client = factory(model=IncompleteCacheModel(), ready=False)
    for _ in range(200):
        response = client.get("/health/ready")
        if response.json().get("status") == "startup_or_storage_failure":
            break
        time.sleep(0.01)
    assert response.status_code == 503
    assert response.json() == {"status": "startup_or_storage_failure"}
    assert not client.app.state.coordinator.ready


def test_forwarding_headers_cannot_create_new_rate_limit_identity(factory):
    client = factory(login_rate_limit=1)
    register(client)
    response = client.post(
        "/api/v1/auth/register",
        json={"login_id": "another-user", "password": "correct-horse-battery"},
        headers={"CloudFront-Viewer-Address": "192.0.2.10:1234", "X-Forwarded-For": "192.0.2.11"},
    )
    assert response.status_code == 429


def test_routers_keep_each_application_settings_and_storage_isolated(factory, tmp_path):
    first = factory(data_dir=tmp_path / "first", source_max_chars=3)
    second = factory(data_dir=tmp_path / "second", source_max_chars=100)
    register(first, "alice")
    register(second, "bob")
    assert first.get("/api/v1/auth/me").json()["source_max_chars"] == 3
    assert second.get("/api/v1/auth/me").json()["source_max_chars"] == 100
    payload = {"source_code": "value = 1"}
    assert first.post("/api/v1/reviews", json=payload).status_code == 422
    response = second.post("/api/v1/reviews", json=payload)
    assert response.status_code == 202
    review_id = response.json()["review_id"]
    assert wait_review(second, review_id)["status"] == "completed"
    assert first.get("/api/v1/reviews").json()["items"] == []
    assert first.get(f"/api/v1/reviews/{review_id}").status_code == 404
