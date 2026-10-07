"""Review model configuration shared by runtime, deployment and evaluation.

Ollama owns tokenization and chat templates. These settings describe request
budgets and sampling; no model family or tokenizer revision is baked into code.
"""

from pydantic import BaseModel, Field, model_validator


class ModelSettings(BaseModel):
    ollama_base_url: str = "http://review-ollama.local-inference.svc.cluster.local:11434"
    ollama_model: str = Field(
        default="qwen3:1.7b", max_length=128, pattern=r"^[a-zA-Z0-9._/-]+:[a-zA-Z0-9._-]+$"
    )
    ollama_model_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    model_context_tokens: int = Field(default=4096, ge=256, le=131072)
    model_max_input_tokens: int = Field(default=2048, ge=128, le=131072)
    model_max_output_tokens: int = Field(default=512, ge=3, le=32768)
    model_keep_alive: str = Field(default="5m", pattern=r"^(0|[1-9][0-9]*[smh])$")
    model_temperature: float = Field(default=0.7, ge=0, le=2)
    model_top_p: float = Field(default=0.8, gt=0, le=1)
    model_top_k: int = Field(default=20, ge=0, le=1000)
    model_min_p: float = Field(default=0, ge=0, le=1)
    model_repeat_penalty: float = Field(default=1, gt=0, le=2)
    model_inference_concurrency: int = Field(default=1, ge=1, le=1)
    inference_timeout_seconds: float = Field(default=180, gt=0, le=600)

    @model_validator(mode="after")
    def validate_context_budget(self):
        if self.model_max_input_tokens + self.model_max_output_tokens > self.model_context_tokens:
            raise ValueError("MODEL_CONTEXT_TOKENS must cover the input and output token budgets.")
        return self

    @property
    def generation_parameters(self) -> dict[str, float | int]:
        return {
            "temperature": self.model_temperature,
            "top_p": self.model_top_p,
            "top_k": self.model_top_k,
            "min_p": self.model_min_p,
            "repeat_penalty": self.model_repeat_penalty,
        }
