"""Public model identity, derived from the actual adapter rather than deployment mode."""

from typing import Literal, NotRequired, TypedDict

from app.config import Settings
from app.inference.model import ReviewModel, TransformersModel
from app.inference.ollama import OllamaModel


class ModelIdentity(TypedDict):
    inference_mode: Literal["real", "simulated", "unknown"]
    model_id: str | None
    model_revision: str | None
    model_source: Literal["backend_configuration", "ollama_api", "test_fixture", "unknown"]
    device: Literal["cpu", "gpu", "mixed"] | None
    inference_backend: NotRequired[str]
    quantization: NotRequired[str | None]


def model_identity(model: ReviewModel, settings: Settings) -> ModelIdentity:
    if isinstance(model, OllamaModel):
        return {
            "inference_mode": "real",
            "model_id": settings.ollama_model,
            "model_revision": model.digest,
            "model_source": "ollama_api",
            "device": model.device,
            "inference_backend": "ollama",
            "quantization": model.quantization,
        }
    if isinstance(model, TransformersModel):
        return {
            "inference_mode": "real",
            "model_id": settings.model_id,
            "model_revision": settings.model_revision,
            "model_source": "backend_configuration",
            "device": "cpu",
        }
    if getattr(model, "simulated", False) is True:
        return {
            "inference_mode": "simulated",
            "model_id": "Simulated model",
            "model_revision": "fixture-v1",
            "model_source": "test_fixture",
            "device": None,
        }
    return {
        "inference_mode": "unknown",
        "model_id": None,
        "model_revision": None,
        "model_source": "unknown",
        "device": None,
    }
