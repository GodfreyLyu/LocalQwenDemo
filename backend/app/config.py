import ipaddress
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.model_config import ModelSettings
from app.persistence.local_dynamodb import validate_local_endpoint


class Settings(ModelSettings, BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    environment: Literal["local", "test"] = "local"
    deployment_environment: Literal["minikube", "development", "unknown"] = "unknown"
    enable_api_docs: bool = False
    release_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,64}$")
    signing_secret: SecretStr
    allowed_origin: str = "http://localhost:5173"
    cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=1800, ge=60, le=3600)
    data_dir: Path = Path("/data")
    aws_region: str = "ap-northeast-1"
    dynamodb_table: str = "llm-review-users"
    dynamodb_endpoint_url: str
    inference_drain_seconds: float = Field(default=30, gt=0, le=120)
    shutdown_grace_seconds: float = Field(default=20, gt=0, le=60)
    source_max_chars: int = Field(default=12000, ge=1, le=32000)
    queue_capacity: int = Field(default=8, ge=1, le=32)
    max_retries: int = Field(default=1, ge=0, le=2)
    login_rate_limit: int = Field(default=10, ge=1, le=100)
    submission_rate_limit: int = Field(default=6, ge=1, le=60)

    @model_validator(mode="after")
    def secure_configuration(self):
        if len(self.signing_secret.get_secret_value()) < 32:
            raise ValueError("SIGNING_SECRET must contain at least 32 characters.")
        if self.allowed_origin.endswith("/"):
            raise ValueError("ALLOWED_ORIGIN must not end with a slash.")
        url = urlsplit(self.ollama_base_url)
        allowed_host = url.hostname in {
            "localhost",
            "host.minikube.internal",
            "host.docker.internal",
        }
        # Fully qualified Kubernetes Service DNS; retain the fixed HTTP port and
        # reject arbitrary external DNS names and suffix lookalikes.
        dns_label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        allowed_host = allowed_host or bool(
            re.fullmatch(rf"{dns_label}\.{dns_label}\.svc\.cluster\.local", url.hostname or "")
        )
        try:
            address = ipaddress.ip_address(url.hostname or "")
            allowed_host = address.is_loopback or any(
                address in ipaddress.ip_network(n)
                for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
        except ValueError:
            pass
        if (
            url.scheme != "http"
            or not allowed_host
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
            or url.port != 11434
        ):
            raise ValueError("OLLAMA_BASE_URL must be a local HTTP endpoint on port 11434.")
        self.ollama_base_url = self.ollama_base_url.rstrip("/")
        validate_local_endpoint(self.dynamodb_endpoint_url)
        return self
