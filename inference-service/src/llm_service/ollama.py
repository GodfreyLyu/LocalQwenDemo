"""Native loopback HTTP adapter. No application prompts or response formatting."""

import asyncio
import json
import re
import threading
import time

import httpx
from llm_contract.generated.ttypes import GenerationResult, TokenUsage

from llm_service.errors import ExecutionError, ExecutionUncertain
from llm_service.errors import ExecutionError as failure


class OllamaAdapter:
    def __init__(self, settings, process):
        self.settings, self.process = settings, process
        self.quantization = None
        self.device = None
        self.loaded = threading.Event()

    def ready(self):
        return self.loaded.is_set() and self.process.alive() and not self.process.closing.is_set()

    def _client(self):
        return httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{self.settings.ollama_port}",
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5, connect=2),
        )

    def _status(self, response):
        if response.status_code != 200:
            # Recognize context errors, but never return native text to a caller.
            if "the input length exceeds the context length" in response.text:
                raise failure("input_limit", "Messages exceed the model context limit.")
            raise failure("ollama_unavailable", "Native model request failed.", True)

    async def _json(self, client, method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        self._status(response)
        data = response.json()
        if not isinstance(data, dict) or "error" in data:
            raise failure("invalid_response", "Invalid native model response.")
        return data

    async def _identity(self, client):
        s = self.settings
        tags = await self._json(client, "GET", "/api/tags")
        matches = [m for m in tags.get("models", []) if m.get("name") == s.model_name]
        if not matches:
            raise failure("model_missing", "Configured model is not installed.")
        if (
            len(matches) != 1
            or matches[0].get("remote_host")
            or "sha256:" + matches[0].get("digest", "").removeprefix("sha256:") != s.model_digest
        ):
            raise failure("model_mismatch", "Installed model differs from the pinned revision.")

    async def _device(self, client):
        # Native residency metadata may lag a just-completed warmup briefly.
        deadline = time.monotonic() + 2
        while True:
            data = await self._json(client, "GET", "/api/ps")
            matches = [
                m for m in data.get("models", []) if m.get("name") == self.settings.model_name
            ]
            if matches or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        if len(matches) != 1:
            raise failure("model_mismatch", "Configured model is not resident.")
        model = matches[0]
        size, vram = model.get("size"), model.get("size_vram")
        if (
            "sha256:" + model.get("digest", "").removeprefix("sha256:")
            != self.settings.model_digest
            or type(size) is not int
            or size <= 0
            or type(vram) is not int
            or vram < 0
        ):
            raise failure("model_mismatch", "Invalid resident model identity.")
        self.device = "gpu" if vram >= size else "cpu" if vram == 0 else "mixed"
        if self.settings.require_gpu and self.device != "gpu":
            raise failure("gpu_required", "Full GPU residency is required by this service.")

    async def _load(self):
        s = self.settings
        deadline = time.monotonic() + s.model_wait_seconds
        async with self._client() as client:
            while time.monotonic() < deadline and not self.process.closing.is_set():
                if not self.process.alive():
                    raise failure("ollama_unavailable", "Ollama child exited.")
                try:
                    version = await self._json(client, "GET", "/api/version")
                    break
                except httpx.RequestError:
                    await asyncio.sleep(0.1)
            else:
                raise failure("ollama_unavailable", "Ollama startup timed out.")
            match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version.get("version", ""))
            if not match or tuple(map(int, match.groups())) < (0, 24, 0):
                raise failure("version_unsupported", "Ollama 0.24.0 or newer is required.")
            try:
                await self._identity(client)
            except ExecutionError as exc:
                if exc.code != "model_missing" or not s.model_pull_if_missing:
                    raise
                await self._json(
                    client,
                    "POST",
                    "/api/pull",
                    json={"model": s.model_name, "stream": False},
                    timeout=max(0.01, deadline - time.monotonic()),
                )
                await self._identity(client)
            shown = await self._json(client, "POST", "/api/show", json={"model": s.model_name})
            info = shown.get("model_info", {})
            context = info.get(f"{info.get('general.architecture')}.context_length")
            if (
                "completion" not in shown.get("capabilities", [])
                or shown.get("remote_host")
                or type(context) is not int
                or context < s.model_context
            ):
                raise failure("model_mismatch", "Model does not support the configured context.")
            self.quantization = shown.get("details", {}).get("quantization_level")
            # A generic probe also verifies that the runner can actually execute.
            await self._chat(
                client,
                [{"role": "user", "content": "Reply with Ready."}],
                {"max_output_tokens": 8, "temperature": 0, "top_p": 1, "seed": 0},
            )
            await self._device(client)

    def load(self):
        self.loaded.clear()

        async def bounded_load():
            async with asyncio.timeout(self.settings.model_wait_seconds + 180):
                await self._load()

        asyncio.run(bounded_load())
        if not self.process.alive():
            raise failure("ollama_unavailable", "Supervised Ollama child exited during startup.")
        self.loaded.set()
        self.process.recovering.clear()

    async def _chat(self, client, messages, options):
        s = self.settings
        started = time.monotonic()
        payload = {
            "model": s.model_name,
            "messages": messages,
            "think": False,
            "stream": True,
            "keep_alive": "5m",
            "truncate": False,
            "shift": False,
            "options": {
                "num_ctx": s.model_context,
                "num_predict": options["max_output_tokens"],
                "temperature": options["temperature"],
                "top_p": options["top_p"],
                "num_thread": s.model_threads,
                "top_k": 20,
                "min_p": 0,
                "repeat_penalty": 1,
            },
        }
        if options.get("seed") is not None:
            payload["options"]["seed"] = options["seed"]
        if s.require_gpu:
            payload["options"]["num_gpu"] = 999
        fragments, received, content_chars, first = [], 0, 0, None
        async with client.stream("POST", "/api/chat", json=payload, timeout=None) as response:
            if response.status_code != 200:
                await response.aread()
                self._status(response)
            async for line in response.aiter_lines():
                if not line:
                    continue
                received += len(line)
                if received > 2_000_000:
                    raise failure("output_limit", "Native response exceeds the service limit.")
                item = json.loads(line)
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("error"), str)
                    and "the input length exceeds the context length" in item["error"]
                ):
                    raise failure("input_limit", "Messages exceed the model context limit.")
                if (
                    not isinstance(item, dict)
                    or item.get("error")
                    or item.get("model") != s.model_name
                    or item.get("remote_host")
                ):
                    raise failure("invalid_response", "Invalid native generation response.")
                message = item.get("message", {})
                content = message.get("content", "")
                if (
                    not isinstance(content, str)
                    or message.get("thinking")
                    or message.get("tool_calls")
                ):
                    raise failure("invalid_response", "Unsupported native generation response.")
                if content:
                    if first is None:
                        first = round((time.monotonic() - started) * 1000)
                    fragments.append(content)
                content_chars += len(content)
                if content_chars > s.max_result_chars:
                    raise failure("output_limit", "Generated content exceeds the service limit.")
                if item.get("done") is True:
                    inputs, outputs = item.get("prompt_eval_count"), item.get("eval_count")
                    if (
                        type(inputs) is not int
                        or inputs <= 0
                        or type(outputs) is not int
                        or not 0 <= outputs <= options["max_output_tokens"]
                    ):
                        raise failure("invalid_response", "Invalid native token usage.")
                    if inputs > s.max_input_tokens:
                        raise failure(
                            "input_limit", "Messages exceed the service input token limit."
                        )
                    reason = item.get("done_reason")
                    if reason not in {"stop", "length"}:
                        raise failure("invalid_response", "Invalid native finish reason.")
                    return GenerationResult(
                        content="".join(fragments),
                        model_id=s.model_name,
                        model_revision=s.model_digest,
                        usage=TokenUsage(input_tokens=inputs, output_tokens=outputs),
                        finish_reason=reason,
                        generation_ms=round((time.monotonic() - started) * 1000),
                        first_token_ms=first,
                    )
        raise failure("invalid_response", "Native response ended before completion.")

    def generate(self, request, stop):
        s = self.settings
        values = vars(request.options) if request.options else {}
        options = {
            key: values.get(key) if values.get(key) is not None else default
            for key, default in (
                ("max_output_tokens", s.default_output_tokens),
                ("temperature", s.temperature),
                ("top_p", s.top_p),
                ("seed", None),
            )
        }

        async def operation():
            async with self._client() as client:
                await self._identity(client)
                result = await self._chat(client, [vars(m) for m in request.messages], options)
                await self._identity(client)
                await self._device(client)
                return result

        async def bounded():
            task = asyncio.create_task(operation())
            try:
                while not task.done():
                    if stop.is_set() or self.process.closing.is_set():
                        raise failure("cancelled", "Generation cancellation requested.")
                    await asyncio.sleep(0.02)
                return await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        try:
            return asyncio.run(bounded())
        except Exception as error:
            # Closing HTTP alone cannot prove runner exit. Reap Ollama before returning,
            # including on malformed output or a native connection failure.
            self.loaded.clear()
            try:
                self.process.abort()
            except Exception:
                self.process.closing.set()
                raise ExecutionUncertain() from None
            if not self.process.closing.is_set():
                try:
                    self.process.start()
                    self.load()
                except Exception:
                    self.process.closing.set()
            if isinstance(error, ExecutionError):
                raise
            raise failure("execution_failed", "Native generation failed.") from None
