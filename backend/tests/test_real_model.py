import os
import resource
import sys
import threading
import time
from pathlib import Path

import pytest

from app.config import MODEL_REVISION, Settings
from app.errors import AppError
from app.inference.model import TransformersModel


@pytest.mark.real_model
@pytest.mark.skipif(
    os.environ.get("RUN_REAL_MODEL") != "1", reason="Set RUN_REAL_MODEL=1 for opt-in real inference"
)
def test_pinned_real_model(record_property):
    started = time.perf_counter()
    settings = Settings(
        signing_secret="local-smoke-only-" * 3,
        dynamodb_endpoint_url="http://127.0.0.1:8001",
        hf_home=Path(os.environ.get("HF_HOME", "../.local/models/huggingface")),
        model_max_output_tokens=64,
        inference_timeout_seconds=180,
    )
    model = TransformersModel(settings)
    model.load()
    loaded = time.perf_counter()
    assert settings.model_id == "Qwen/Qwen3-1.7B"
    assert settings.model_revision == MODEL_REVISION
    assert model.count_tokens("def average(xs): return sum(xs) / len(xs)", "python") < 2048
    try:
        result = model.review(
            "def average(xs): return sum(xs) / len(xs)", "python", threading.Event()
        )
        assert result.strip()
        inference_outcome = "completed"
    except AppError as error:
        # The deliberately small smoke budget may still produce a controlled gate rejection.
        assert error.code == "invalid_model_response"
        inference_outcome = error.code
    assert model.model.training is False and str(model.model.device) == "cpu"
    rss_divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    record_property(
        "peak_rss_mib", round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_divisor, 1)
    )
    record_property("load_seconds", round(loaded - started, 2))
    record_property("inference_seconds", round(time.perf_counter() - loaded, 2))
    record_property("model_id", settings.model_id)
    record_property("model_revision", settings.model_revision)
    record_property("dtype", settings.model_dtype)
    record_property("inference_outcome", inference_outcome)
