import time

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from backend.tests.support import FakeModel, register, wait_review


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
@pytest.mark.contract
def test_inference_handshake_failure_keeps_public_readiness_contract(factory):

    class UnavailableService:
        def load(self):
            raise AppError("ollama_unavailable", "Inference service is unavailable.", 503)

    client = factory(model=UnavailableService(), ready=False)
    for _ in range(200):
        response = client.get("/health/ready")
        if response.json().get("status") == "startup_or_storage_failure":
            break
        time.sleep(0.01)
    assert response.status_code == 503
    assert response.json() == {"status": "startup_or_storage_failure"}
    assert not client.app.state.coordinator.ready
