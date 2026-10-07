"""Native Ollama inference with server-owned tokenization and bounded cancellation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time

import httpx

from app.config import Settings
from app.errors import AppError
from app.inference.generation import (
    GenerationMetrics,
    SectionMetrics,
    allocate_section_token_limits,
    check_deadline,
    derive_generation_seed,
)
from app.inference.prompts import SECTION_SPECS, build_prompt_messages
from app.inference.review_output import (
    normalize_section_body,
    trim_capped_section_tail,
    validate_review_output,
)
from app.startup import startup_stage

logger = logging.getLogger("review")
MIN_OLLAMA_VERSION = (0, 24, 0)
CONTEXT_ERROR = "the input length exceeds the context length"


def invalid_model() -> AppError:
    return AppError("ollama_model_mismatch", "Ollama model does not match configuration.", 503)


def token_limit() -> AppError:
    return AppError("token_limit", "Source exceeds the model input token limit.", 422)


def check_server_error(value: dict) -> None:
    # Do not expose arbitrary server messages (which may contain user content).
    error = value.get("error")
    if isinstance(error, str) and CONTEXT_ERROR in error:
        raise token_limit()
    if "error" in value:
        raise AppError("invalid_model_response", "Ollama returned an invalid response.", 502)


class OllamaModel:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings.model_copy(deep=True)
        self.digest: str | None = None
        self.quantization: str | None = None
        self.device: str | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.ollama_base_url,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(self.settings.inference_timeout_seconds, connect=5),
        )

    @staticmethod
    def _status(response: httpx.Response) -> None:
        if response.status_code == 404:
            raise AppError(
                "ollama_model_missing", "The configured Ollama model is not installed.", 503
            )
        if response.status_code != 200:
            raise AppError("ollama_unavailable", "Ollama could not process the request.", 503)

    async def _json(self, client, method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        self._status(response)
        value = response.json()
        if not isinstance(value, dict) or "error" in value:
            raise AppError("invalid_model_response", "Ollama returned an invalid response.", 502)
        return value

    async def _identity(self, client) -> str:
        data = await self._json(client, "GET", "/api/tags")
        matches = [m for m in data.get("models", []) if m.get("name") == self.settings.ollama_model]
        if not matches:
            raise AppError(
                "ollama_model_missing", "The configured Ollama model is not installed.", 503
            )
        if len(matches) != 1 or matches[0].get("remote_host"):
            raise invalid_model()
        digest = matches[0].get("digest", "").removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise invalid_model()
        digest = "sha256:" + digest
        if (self.digest and digest != self.digest) or (
            self.settings.ollama_model_digest and digest != self.settings.ollama_model_digest
        ):
            raise invalid_model()
        return digest

    def _run(self, operation, stop: threading.Event, *, timeout: float | None = None):
        async def bounded():
            async def cancelled():
                while not stop.is_set():
                    await asyncio.sleep(0.05)

            request = asyncio.create_task(operation())
            cancellation = asyncio.create_task(cancelled())
            try:
                done, _ = await asyncio.wait(
                    [request, cancellation],
                    timeout=self.settings.inference_timeout_seconds if timeout is None else timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if request not in done or stop.is_set():
                    raise AppError(
                        "inference_timeout", "Review timed out. Try a shorter submission.", 504
                    )
                return await request
            finally:
                for task in (request, cancellation):
                    task.cancel()
                await asyncio.gather(request, cancellation, return_exceptions=True)

        try:
            return asyncio.run(bounded())
        except httpx.TimeoutException:
            raise AppError("inference_timeout", "Ollama request timed out.", 504) from None
        except httpx.RequestError:
            raise AppError(
                "ollama_unavailable", "Cannot connect to the local Ollama service.", 503
            ) from None
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise AppError(
                "invalid_model_response", "Ollama returned an invalid response.", 502
            ) from None

    def _startup_metadata(self, operation):
        # New Pod DNS/network policy propagation can lag container startup. Retry
        # only connection failures, never missing models or incompatible identities.
        deadline = time.monotonic() + 60
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._run(
                    operation,
                    threading.Event(),
                    timeout=min(15, max(0.01, deadline - time.monotonic())),
                )
            except AppError as error:
                remaining = deadline - time.monotonic()
                if error.code not in {"ollama_unavailable", "inference_timeout"} or remaining <= 0:
                    raise
                delay = min(2 ** min(attempt - 1, 3), remaining)
                logger.warning(
                    "ollama_startup_retry",
                    extra={
                        "stage": "ollama_validation",
                        "error_code": error.code,
                        "startup_attempt": attempt,
                        "startup_wait_ms": round(delay * 1000),
                    },
                )
                time.sleep(delay)

    def load(self) -> None:
        async def metadata():
            async with self._client() as client:
                version = await self._json(client, "GET", "/api/version")
                match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version.get("version", ""))
                if not match or tuple(map(int, match.groups())) < MIN_OLLAMA_VERSION:
                    raise AppError(
                        "ollama_version_unsupported",
                        "Ollama 0.24.0 or newer is required for context protection.",
                        503,
                    )
                self.digest = await self._identity(client)
                return await self._json(
                    client,
                    "POST",
                    "/api/show",
                    json={
                        "model": self.settings.ollama_model,
                    },
                )

        with startup_stage("ollama_validation"):
            shown = self._startup_metadata(metadata)
            info = shown.get("model_info", {})
            architecture = info.get("general.architecture")
            context = info.get(f"{architecture}.context_length")
            if (
                "completion" not in shown.get("capabilities", [])
                or shown.get("remote_host")
                or type(context) is not int
                or context < self.settings.model_context_tokens
            ):
                raise invalid_model()
            self.quantization = shown.get("details", {}).get("quantization_level")

        async def probe():
            async with self._client() as client:
                await self._identity(client)
                messages = build_prompt_messages(
                    "def startup_probe(value):\n    return value\n", "python", "summary"
                )
                await self._chat(client, messages, 1, 0, SectionMetrics())
                await self._observe_device(client)

        with startup_stage("startup_generation"):
            self._run(probe, threading.Event())

    async def _observe_device(self, client):
        self.device = None
        data = await self._json(client, "GET", "/api/ps")
        for model in data.get("models", []):
            if "sha256:" + model.get("digest", "").removeprefix("sha256:") == self.digest:
                size, vram = model.get("size"), model.get("size_vram")
                if type(size) is int and size > 0 and type(vram) is int and vram >= 0:
                    self.device = "cpu" if vram == 0 else "gpu" if vram >= size else "mixed"

    def count_tokens(self, source: str, language: str) -> None:
        # The public Ollama API has no tokenize endpoint. Admission checks characters;
        # authoritative token checks happen in the serial worker, using Ollama's
        # native template/tokenizer. Never estimate an exact count with a different tokenizer.
        return None

    async def _chat(self, client, messages, limit, seed, metrics):
        started = time.monotonic()
        payload = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "think": False,
            "stream": True,
            "keep_alive": self.settings.model_keep_alive,
            "truncate": False,
            "shift": False,
            "options": {
                "num_ctx": self.settings.model_context_tokens,
                "num_predict": limit,
                "seed": seed,
                **self.settings.generation_parameters,
            },
        }
        fragments, finished, received = [], False, 0
        try:
            async with client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code != 200:
                    await response.aread()
                    try:
                        value = response.json()
                    except ValueError:
                        value = {}
                    if isinstance(value, dict) and isinstance(value.get("error"), str):
                        if CONTEXT_ERROR in value["error"]:
                            raise token_limit()
                    self._status(response)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    received += len(line)
                    if received > 1_000_000:
                        raise invalid_model()
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise invalid_model()
                    check_server_error(item)
                    if item.get("model") != self.settings.ollama_model or item.get("remote_host"):
                        raise AppError(
                            "invalid_model_response", "Ollama returned an invalid response.", 502
                        )
                    message = item.get("message", {})
                    content = message.get("content", "")
                    if (
                        not isinstance(content, str)
                        or message.get("thinking")
                        or message.get("tool_calls")
                    ):
                        raise AppError(
                            "invalid_model_response", "Ollama returned unexpected output.", 502
                        )
                    if content:
                        if metrics.first_token_ms is None:
                            metrics.first_token_ms = round((time.monotonic() - started) * 1000)
                        fragments.append(content)
                    if item.get("done") is True:
                        count = item.get("prompt_eval_count")
                        if type(count) is not int or count <= 0:
                            raise invalid_model()
                        metrics.input_tokens = count
                        if count > self.settings.model_max_input_tokens:
                            raise token_limit()
                        count = item.get("eval_count")
                        if type(count) is not int or not 0 <= count <= limit:
                            raise invalid_model()
                        metrics.generated_tokens = count
                        metrics.limit_reached = (
                            count >= limit or item.get("done_reason") == "length"
                        )
                        finished = True
                        break
            if not finished:
                raise AppError(
                    "invalid_model_response", "Ollama response ended before completion.", 502
                )
            return "".join(fragments)
        finally:
            metrics.generation_ms = round((time.monotonic() - started) * 1000)

    def review(self, source: str, language: str, stop: threading.Event) -> str:
        metrics = GenerationMetrics(
            output_token_limit=self.settings.model_max_output_tokens,
            worker_intraop_threads=None,
            worker_interop_threads=None,
            sections={s: SectionMetrics() for s, _, _ in SECTION_SPECS},
        )
        limits = allocate_section_token_limits(self.settings.model_max_output_tokens)
        deadline = time.monotonic() + self.settings.inference_timeout_seconds

        async def generate():
            async with self._client() as client:
                await self._identity(client)
                bodies = []
                for section, title, _ in SECTION_SPECS:
                    check_deadline(stop, deadline)
                    await self._identity(client)
                    section_metrics = metrics.sections[section]
                    decoded = await self._chat(
                        client,
                        build_prompt_messages(source, language, section),
                        limits[section],
                        derive_generation_seed(self.digest, language, section, source),
                        section_metrics,
                    )
                    body = normalize_section_body(section, decoded)
                    body, section_metrics.trailing_fragment_removed = trim_capped_section_tail(
                        body,
                        section_metrics.limit_reached,
                    )
                    bodies.append(f"## {title}\n{body}")
                await self._identity(client)
                await self._observe_device(client)
                return validate_review_output("\n\n".join(bodies), source)

        try:
            return self._run(generate, stop)
        finally:
            metrics.duration_seconds = sum(
                (s.generation_ms or 0) / 1000 for s in metrics.sections.values()
            )
            logger.info("model_generation_finished", extra=metrics.fields())
