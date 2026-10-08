import time

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from backend.tests.support import FakeModel, register, wait_review


@pytest.mark.component
def test_readiness_while_model_loading(factory):
    class Loading(FakeModel):
        def load(self):
            time.sleep(0.2)

    client = factory(Loading(), ready=False)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


@pytest.mark.component
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


@pytest.mark.component
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


@pytest.mark.component
@pytest.mark.contract
def test_ollama_load_failure_keeps_public_readiness_contract(factory):

    class IncompleteCacheModel:
        def load(self):
            raise AppError("ollama_model_missing", "Configured model is missing.", 503)

    client = factory(model=IncompleteCacheModel(), ready=False)
    for _ in range(200):
        response = client.get("/health/ready")
        if response.json().get("status") == "startup_or_storage_failure":
            break
        time.sleep(0.01)
    assert response.status_code == 503
    assert response.json() == {"status": "startup_or_storage_failure"}
    assert not client.app.state.coordinator.ready
