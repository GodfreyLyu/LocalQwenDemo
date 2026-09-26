"""Observed lifecycle behavior with controlled worker exits, real SQLite and HTTP."""

import asyncio
import threading
import time

import pytest
from conftest import FakeModel, register, wait_ready, wait_review
from fastapi.testclient import TestClient


def wait_status(client, status):
    for _ in range(200):
        response = client.get("/health/ready")
        if response.json()["status"] == status:
            return response
        time.sleep(0.01)
    raise AssertionError(f"Readiness did not become {status}")


class ControlledModel(FakeModel):
    def __init__(self, *, late_error=False):
        super().__init__()
        self.release = threading.Event()
        self.started = threading.Event()
        self.late_error = late_error

    def review(self, source, language, stop):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.calls == 1:
                self.started.set()
                assert self.release.wait(5)
                if self.late_error:
                    raise RuntimeError("PRIVATE_LATE_FAILURE")
            return "Review complete."
        finally:
            self.active -= 1


def queue_two_users(client, model):
    register(client)
    other = TestClient(
        client.app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    )
    register(other, "bob")
    first = client.post("/api/v1/reviews", json={"source_code": "first"})
    assert first.status_code == 202
    assert model.started.wait(2)
    second = other.post("/api/v1/reviews", json={"source_code": "second"})
    assert second.status_code == 202
    return first.json()["review_id"], second.json()["review_id"], other


@pytest.mark.parametrize("late_error", [False, True])
def test_drain_blocks_next_job_then_recovers_without_overwriting_timeout(factory, late_error):
    model = ControlledModel(late_error=late_error)
    client = factory(model, inference_timeout_seconds=0.3, inference_drain_seconds=2)
    try:
        first_id, second_id, other = queue_two_users(client, model)
        failed = wait_review(client, first_id)
        assert failed["error_code"] == "inference_timeout"
        assert wait_status(client, "inference_draining").status_code == 503
        assert client.get("/health/live").status_code == 200
        assert other.get(f"/api/v1/reviews/{second_id}").json()["status"] == "queued"
        assert client.post("/api/v1/reviews", json={"source_code": "third"}).status_code == 503
        assert model.calls == 1
        model.release.set()
        assert wait_review(other, second_id)["status"] == "completed"
        wait_ready(client)
        assert client.get(f"/api/v1/reviews/{first_id}").json() == failed
        assert model.calls == 2 and model.peak == 1
    finally:
        model.release.set()


def test_stuck_worker_remains_unhealthy_after_late_exit_and_leaves_queue_intact(factory):
    model = ControlledModel()
    client = factory(model, inference_timeout_seconds=0.3, inference_drain_seconds=0.05)
    try:
        first_id, second_id, other = queue_two_users(client, model)
        assert wait_review(client, first_id)["error_code"] == "inference_timeout"
        assert wait_status(client, "inference_stuck").status_code == 503
        assert client.get("/health/live").status_code == 503
        model.release.set()
        for _ in range(200):
            if model.active == 0:
                break
            time.sleep(0.01)
        assert model.active == 0 and model.calls == 1
        assert client.get("/health/ready").json()["status"] == "inference_stuck"
        assert other.get(f"/api/v1/reviews/{second_id}").json()["status"] == "queued"
    finally:
        model.release.set()


def test_shutdown_during_model_load_cannot_reopen_admission(factory):
    release = threading.Event()
    started = threading.Event()

    class LoadingModel(FakeModel):
        def load(self):
            started.set()
            assert release.wait(5)

    client = factory(LoadingModel(), ready=False)
    try:
        assert started.wait(2)
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").json()["status"] == "model_loading"
        coordinator = client.app.state.coordinator

        async def close_after_releasing_load():
            close = asyncio.create_task(coordinator.close())
            await asyncio.sleep(0)
            release.set()
            await close

        client.portal.call(close_after_releasing_load)
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200
        register(client)
        assert client.post("/api/v1/reviews", json={"source_code": "x"}).status_code == 503
    finally:
        release.set()
