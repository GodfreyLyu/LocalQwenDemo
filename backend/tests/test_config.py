from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import Settings


def test_kubernetes_config_map_values_load_from_environment(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / "deploy/kustomize/base/config.yaml").read_text())["data"]
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    settings = Settings(signing_secret="test-secret-" * 4)
    assert settings.dynamodb_endpoint_url == "http://review-dynamodb:8000"
    assert settings.environment == "local"
    assert settings.cookie_secure is False
    assert settings.model_inference_concurrency == 1
    assert settings.model_id == "Qwen/Qwen3-1.7B"
    assert settings.model_revision == "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
    assert settings.model_max_output_tokens == 384
    assert settings.inference_timeout_seconds == 300
    assert settings.data_dir == Path("/data")
    assert settings.hf_home == Path("/models/huggingface")
    assert settings.release_sha is None


def test_release_sha_is_optional_but_must_be_a_git_style_hex_digest():
    base = {"signing_secret": "test-secret-" * 4, "dynamodb_endpoint_url": "http://127.0.0.1:8001"}
    assert Settings(**base, release_sha="abcdef012345").release_sha == "abcdef012345"
    with pytest.raises(ValidationError):
        Settings(**base, release_sha="not-a-release")


@pytest.mark.parametrize("concurrency", ["0", "2", "8"])
def test_environment_cannot_enable_parallel_inference(monkeypatch, concurrency):
    monkeypatch.setenv("MODEL_INFERENCE_CONCURRENCY", concurrency)
    with pytest.raises(ValidationError):
        Settings(dynamodb_endpoint_url="http://127.0.0.1:8001", signing_secret="test-secret-" * 4)


def test_removed_cloud_environment_is_rejected_without_leaking_signing_secret():
    secret = "this-secret-must-not-appear-in-startup-logs"
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            cookie_secure=False,
            signing_secret=secret,
            dynamodb_endpoint_url="http://127.0.0.1:8001",
        )
    assert secret not in str(error.value)


def test_runtime_model_substitution_is_rejected():
    with pytest.raises(ValidationError):
        Settings(
            dynamodb_endpoint_url="http://127.0.0.1:8001",
            signing_secret="test-secret-" * 4,
            model_id="some-other/model",
        )


@pytest.mark.parametrize("output_tokens", [1, 2])
def test_output_budget_must_allow_all_three_sections(output_tokens):
    with pytest.raises(ValidationError):
        Settings(
            dynamodb_endpoint_url="http://127.0.0.1:8001",
            signing_secret="test-secret-" * 4,
            model_max_output_tokens=output_tokens,
        )
