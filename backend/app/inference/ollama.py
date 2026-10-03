"""Native Ollama inference with a verified Qwen tokenizer and bounded cancellation.

Only the stock Qwen3 template below is supported. A changed template/vocabulary
fails startup rather than estimating tokens or silently truncating a submission.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time

import httpx

from app.config import MODEL_REVISION, Settings
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
TEMPLATE_SHA256 = "ae370d884f108d16e7cc8fd5259ebc5773a0afa6e078b11f4ed7e39a27e0dfc4"


def invalid_model() -> AppError:
    return AppError(
        "ollama_model_mismatch", "Ollama model or tokenizer does not match configuration.", 503
    )


def render_prompt(messages: list[dict[str, str]]) -> str:
    # Exact rendering of the hash-checked stock template for our two-message input,
    # without tools, with think=false. Never use the HF chat template for Ollama.
    return (
        "".join(
            f"<|im_start|>{m['role']}\n{m['content']}"
            + (" /no_think" if m["role"] == "user" else "")
            + "<|im_end|>\n"
            for m in messages
        )
        + "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def validate_tokenizer(document: dict, metadata: dict) -> None:
    info = metadata.get("model_info", {})
    template = metadata.get("template", "")
    tokens = info.get("tokenizer.ggml.tokens", [])
    vocab = document["model"]["vocab"]
    added = document["added_tokens"]
    merges = document["model"]["merges"]
    if (
        hashlib.sha256(template.encode()).hexdigest() != TEMPLATE_SHA256
        or info.get("general.architecture") != "qwen3"
        or info.get("general.size_label") != "1.7B"
        or info.get("tokenizer.ggml.pre") != "qwen2"
        or info.get("tokenizer.ggml.add_bos_token") is not False
        or info.get("tokenizer.ggml.eos_token_id") != 151645
        or len(tokens) < len(vocab) + len(added)
        or any(tokens[i] != token for token, i in vocab.items())
        or any(tokens[t["id"]] != t["content"] for t in added)
        or info.get("tokenizer.ggml.merges")
        != [" ".join(m) if isinstance(m, list) else m for m in merges]
        or metadata.get("messages")
        or metadata.get("system")
    ):
        raise invalid_model()


class OllamaModel:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
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
        if self.settings.model_revision != MODEL_REVISION:
            raise invalid_model()

        async def metadata():
            async with self._client() as client:
                self.digest = await self._identity(client)
                return await self._json(
                    client,
                    "POST",
                    "/api/show",
                    json={
                        "model": self.settings.ollama_model,
                        "verbose": True,
                    },
                )

        with startup_stage("ollama_validation"):
            shown = self._startup_metadata(metadata)
        with startup_stage("tokenizer_load"):
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            path = hf_hub_download(
                self.settings.model_id,
                "tokenizer.json",
                revision=self.settings.model_revision,
                cache_dir=str(self.settings.hf_home / "hub"),
            )
            with open(path) as file:
                validate_tokenizer(json.load(file), shown)
            self.tokenizer = Tokenizer.from_file(path)
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

    def _tokens(self, messages):
        return len(self.tokenizer.encode(render_prompt(messages), add_special_tokens=False).ids)

    def count_tokens(self, source: str, language: str) -> int:
        return max(
            self._tokens(build_prompt_messages(source, language, s)) for s, _, _ in SECTION_SPECS
        )

    async def _chat(self, client, messages, limit, seed, metrics):
        metrics.input_tokens = self._tokens(messages)
        if metrics.input_tokens > self.settings.model_max_input_tokens:
            raise AppError("token_limit", "Source exceeds the model input token limit.", 422)
        started = time.monotonic()
        payload = {
            "model": self.settings.ollama_model,
            "messages": messages,
            "think": False,
            "stream": True,
            "keep_alive": "5m",
            "options": {
                "num_ctx": self.settings.model_max_input_tokens
                + self.settings.model_max_output_tokens,
                "num_predict": limit,
                "seed": seed,
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0.0,
                "repeat_penalty": 1.0,
            },
        }
        fragments, finished, received = [], False, 0
        try:
            async with client.stream("POST", "/api/chat", json=payload) as response:
                self._status(response)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    received += len(line)
                    if received > 1_000_000:
                        raise invalid_model()
                    item = json.loads(line)
                    if "error" in item or item.get("model") != self.settings.ollama_model:
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
                        if item.get("prompt_eval_count") != metrics.input_tokens:
                            raise invalid_model()
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
