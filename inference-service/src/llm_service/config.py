"""Service-owned policy; clients discover public limits through getModelInfo."""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    model_name: str = Field(default="qwen3:1.7b", pattern=r"^[a-zA-Z0-9._/-]+:[a-zA-Z0-9._-]+$")
    model_digest: str = Field(
        default="sha256:8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    model_context: int = Field(default=4096, ge=256, le=131072)
    model_threads: int = Field(default=2, ge=1, le=16)
    model_pull_if_missing: bool = False
    model_wait_seconds: float = Field(default=1200, gt=0, le=3600)
    require_gpu: bool = True
    ollama_port: int = Field(default=11434, ge=1, le=65535)
    max_input_tokens: int = Field(default=2048, ge=1, le=131072)
    max_output_tokens: int = Field(default=512, ge=1, le=32768)
    default_output_tokens: int = Field(default=128, ge=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.8, gt=0, le=1)
    max_message_chars: int = Field(default=64000, ge=1, le=128000)
    max_result_chars: int = Field(default=128000, ge=1, le=256000)
    # One Ollama operation permits process-level cancellation without affecting peers.
    active_generations: int = Field(default=1, ge=1, le=1)
    pending_generations: int = Field(default=8, ge=0, le=32)
    retained_results: int = Field(default=128, ge=1, le=1024)
    result_ttl_seconds: float = Field(default=600, gt=0, le=3600)
    max_deadline_seconds: float = Field(default=300, gt=0, le=600)
    thrift_bind_host: str = "127.0.0.1"
    thrift_port: int = Field(default=9091, ge=0, le=65535)
    rpc_connections: int = Field(default=32, ge=1, le=128)
    health_bind_host: str = "127.0.0.1"
    health_port: int = Field(default=11435, ge=0, le=65535)
    shutdown_seconds: float = Field(default=20, gt=0, le=30)

    @model_validator(mode="after")
    def budgets(self):
        if self.max_input_tokens + self.max_output_tokens > self.model_context:
            raise ValueError("Context must cover input and output limits.")
        if self.default_output_tokens > self.max_output_tokens:
            raise ValueError("Default output must fit the service limit.")
        return self
