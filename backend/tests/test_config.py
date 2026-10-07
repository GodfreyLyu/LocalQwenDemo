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
    assert settings.ollama_model == "qwen3:1.7b"
    assert settings.model_context_tokens == 4096
    assert settings.model_max_output_tokens == 384
    assert settings.inference_timeout_seconds == 300
    assert settings.data_dir == Path("/data")
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


def test_model_selection_and_sampling_are_configurable_from_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:1b")
    monkeypatch.setenv("MODEL_CONTEXT_TOKENS", "8192")
    monkeypatch.setenv("MODEL_MAX_INPUT_TOKENS", "4096")
    monkeypatch.setenv("MODEL_TEMPERATURE", "0.1")
    config = Settings(dynamodb_endpoint_url="http://localhost:8001", signing_secret="x" * 32)
    assert config.ollama_model == "llama3.2:1b"
    assert config.model_context_tokens == 8192
    assert config.generation_parameters["temperature"] == 0.1


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_context_tokens": 2048},
        {"model_top_p": 0},
        {"model_temperature": -1},
        {"model_keep_alive": "-1"},
        {"ollama_model": "model-without-tag"},
    ],
)
def test_invalid_model_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(
            dynamodb_endpoint_url="http://localhost:8001", signing_secret="x" * 32, **overrides
        )


@pytest.mark.parametrize("output_tokens", [1, 2])
def test_output_budget_must_allow_all_three_sections(output_tokens):
    with pytest.raises(ValidationError):
        Settings(
            dynamodb_endpoint_url="http://127.0.0.1:8001",
            signing_secret="test-secret-" * 4,
            model_max_output_tokens=output_tokens,
        )
