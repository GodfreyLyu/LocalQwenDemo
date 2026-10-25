"""Opt-in real service test: the service owns and supervises the real Ollama model."""

import os
import threading

import pytest

from app.inference.rpc.client import ThriftReviewModel
from app.inference.settings import InferenceSettings


@pytest.mark.real_model
@pytest.mark.integration
def test_real_review_through_thrift(record_property):
    if os.environ.get("RUN_REAL_MODEL") != "1":
        pytest.skip("Set RUN_REAL_MODEL=1 and start the inference service first")
    model = ThriftReviewModel(
        InferenceSettings(thrift_host=os.environ.get("THRIFT_HOST", "127.0.0.1"))
    )
    model.load()
    result = model.review(
        "def average(values):\n    return sum(values) / len(values)\n", "python", threading.Event()
    )
    assert all(f"## {section}" in result for section in ("Summary", "Findings", "Suggestions"))
    record_property("model_revision", model.digest)
    record_property("device", model.device)


@pytest.mark.real_model
@pytest.mark.integration
def test_real_fastapi_review_persists_remote_identity(factory):
    if os.environ.get("RUN_REAL_MODEL") != "1":
        pytest.skip("Set RUN_REAL_MODEL=1 and start the inference service first")
    import time

    from backend.tests.support import register

    model = ThriftReviewModel(
        InferenceSettings(thrift_host=os.environ.get("THRIFT_HOST", "127.0.0.1"))
    )
    client = factory(model)
    register(client)
    response = client.post(
        "/api/v1/reviews",
        json={
            "source_code": "def average(values):\n    return sum(values) / len(values)\n",
            "language": "python",
        },
    )
    assert response.status_code == 202
    review_id = response.json()["review_id"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = client.get(f"/api/v1/reviews/{review_id}").json()
        if result["status"] in {"completed", "failed"}:
            break
        time.sleep(0.2)
    assert result["status"] == "completed", result.get("error_code")
    assert result["model_revision"] == model.digest
    assert client.get("/api/v1/runtime").json()["inference_mode"] == "real"
