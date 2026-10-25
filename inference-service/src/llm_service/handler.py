"""Bounded generation registry. RPC threads only admit, inspect and cancel jobs."""

import copy
import hashlib
import json
import math
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from llm_contract.generated import LlmService
from llm_contract.generated.ttypes import (
    GenerationOptions,
    GenerationRequest,
    GenerationSnapshot,
    Health,
    Limits,
    LlmError,
    ModelInfo,
)
from llm_contract.generated.ttypes import (
    GenerationState as State,
)

from llm_service.config import Settings
from llm_service.errors import ExecutionError, ExecutionUncertain, failure

TERMINAL = {State.SUCCEEDED, State.FAILED, State.CANCELLED}


@dataclass
class Job:
    request: GenerationRequest
    fingerprint: bytes
    deadline: float
    stop: threading.Event = field(default_factory=threading.Event)
    state: int = State.QUEUED
    result: object = None
    error: LlmError | None = None
    expiry: float | None = None
    expires_at_ms: int | None = None
    cancel_code: str | None = None
    timer: threading.Timer | None = None


class InferenceHandler(LlmService.Iface):
    def __init__(self, model, settings: Settings):
        self.model, self.settings = model, settings
        self.instance_id = str(uuid.uuid4())
        self.condition = threading.Condition()
        self.jobs: dict[str, Job] = {}
        self.pending: deque[Job] = deque()
        self.active: Job | None = None
        self.closing = False
        self.worker = threading.Thread(target=self._work, name="generation", daemon=True)
        self.worker.start()

    def getModelInfo(self):
        s = self.settings
        return ModelInfo(
            instance_id=self.instance_id,
            model_id=s.model_name,
            model_revision=s.model_digest,
            defaults=GenerationOptions(
                max_output_tokens=s.default_output_tokens, temperature=s.temperature, top_p=s.top_p
            ),
            limits=Limits(
                active_generations=1,
                pending_generations=s.pending_generations,
                retained_results=s.retained_results,
                result_ttl_ms=round(s.result_ttl_seconds * 1000),
                max_deadline_ms=round(s.max_deadline_seconds * 1000),
                max_message_chars=s.max_message_chars,
                max_input_tokens=s.max_input_tokens,
                max_output_tokens=s.max_output_tokens,
            ),
            quantization=self.model.quantization,
            device=self.model.device,
        )

    def getHealth(self):
        with self.condition:
            return Health(
                ready=not self.closing and self.model.ready(),
                draining=self.closing,
                active=int(self.active is not None),
                pending=len(self.pending),
            )

    def _instance(self, instance_id):
        if instance_id != self.instance_id:
            raise failure("instance_changed", "The service instance changed; execution is unknown.")

    def _snapshot(self, job):
        return GenerationSnapshot(
            request_id=job.request.request_id,
            instance_id=self.instance_id,
            state=job.state,
            result=copy.deepcopy(job.result),
            error=job.error,
            expires_at_ms=job.expires_at_ms,
        )

    def _expire(self):
        now = time.monotonic()
        for key, job in list(self.jobs.items()):
            if job.expiry is not None and now >= job.expiry:
                del self.jobs[key]

    def _validate(self, request):
        invalid = failure("invalid_request", "Invalid generation request or unsupported option.")
        s = self.settings
        if not isinstance(request, GenerationRequest):
            raise invalid
        self._instance(request.instance_id)
        if not isinstance(request.request_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_.-]{1,128}", request.request_id
        ):
            raise invalid
        if type(request.deadline_ms) is not int or not 0 < request.deadline_ms <= round(
            s.max_deadline_seconds * 1000
        ):
            raise invalid
        if not isinstance(request.messages, list) or not 1 <= len(request.messages) <= 32:
            raise invalid
        size = 0
        for message in request.messages:
            if (
                message.role not in {"system", "user", "assistant"}
                or not isinstance(message.content, str)
                or not message.content.strip()
            ):
                raise invalid
            size += len(message.content)
        if size > s.max_message_chars:
            raise failure("input_limit", "Messages exceed the service character limit.")
        options = request.options or GenerationOptions()
        if not isinstance(options, GenerationOptions):
            raise invalid
        if options.max_output_tokens is not None and (
            type(options.max_output_tokens) is not int
            or not 1 <= options.max_output_tokens <= s.max_output_tokens
        ):
            raise invalid
        for value, lower, upper in ((options.temperature, 0, 2), (options.top_p, 0, 1)):
            if value is not None and (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise invalid
        if options.top_p == 0:
            raise invalid
        if options.seed is not None and (
            type(options.seed) is not int or not 0 <= options.seed < 2**63
        ):
            raise invalid
        # Fingerprint the original payload, including explicit versus omitted options.
        payload = {
            "messages": [vars(m) for m in request.messages],
            "options": vars(options),
            "deadline_ms": request.deadline_ms,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()

    def startGeneration(self, request):
        fingerprint = self._validate(request)
        with self.condition:
            self._expire()
            if previous := self.jobs.get(request.request_id):
                if previous.fingerprint != fingerprint:
                    raise failure(
                        "idempotency_conflict", "Request ID already has a different payload."
                    )
                return self._snapshot(previous)
            if self.closing or not self.model.ready():
                raise failure("unavailable", "Service is recovering or shutting down.", True)
            if len(self.jobs) >= self.settings.retained_results:
                raise failure("retention_full", "Result registry is full; wait for expiry.", True)
            # Reserve a slot for work that has been admitted but not yet taken by the thread.
            occupied = len(self.pending) + int(self.active is not None)
            if occupied >= 1 + self.settings.pending_generations:
                raise failure("overloaded", "Generation capacity is full.", True)
            job = Job(
                copy.deepcopy(request), fingerprint, time.monotonic() + request.deadline_ms / 1000
            )
            self.jobs[request.request_id] = job
            self.pending.append(job)
            job.timer = threading.Timer(request.deadline_ms / 1000, self._deadline, (job,))
            job.timer.daemon = True
            job.timer.start()
            self.condition.notify_all()
            return self._snapshot(job)

    def _find(self, instance_id, request_id):
        self._instance(instance_id)
        self._expire()
        if request_id not in self.jobs:
            raise failure("not_found", "Request is unknown or its result expired.")
        return self.jobs[request_id]

    def getGeneration(self, instance_id, request_id):
        with self.condition:
            return self._snapshot(self._find(instance_id, request_id))

    def _finish(self, job, state, error=None, result=None):
        job.state, job.error, job.result = state, error, result
        if job.timer:
            job.timer.cancel()
        job.expiry = time.monotonic() + self.settings.result_ttl_seconds
        job.expires_at_ms = round((time.time() + self.settings.result_ttl_seconds) * 1000)
        # Only the hash and result remain; drop prompt contents immediately.
        job.request.messages = []

    def _cancel(self, job, code):
        if job.state in TERMINAL:
            return
        job.cancel_code = job.cancel_code or code
        job.stop.set()
        if job.state == State.QUEUED:
            self.pending.remove(job)
            self._finish(
                job, State.CANCELLED, failure(job.cancel_code, "Generation ended before execution.")
            )
        else:
            job.state = State.CANCELLING

    def cancelGeneration(self, instance_id, request_id):
        with self.condition:
            job = self._find(instance_id, request_id)
            self._cancel(job, "cancelled")
            self.condition.notify_all()
            return self._snapshot(job)

    def _deadline(self, job):
        with self.condition:
            self._cancel(job, "deadline_exceeded")

    def _work(self):
        while True:
            with self.condition:
                self._expire()
                for job in list(self.pending):
                    if time.monotonic() >= job.deadline:
                        self._cancel(job, "deadline_exceeded")
                if self.closing and not self.pending:
                    return
                if not self.pending:
                    self.condition.wait(0.05)
                    continue
                job = self.active = self.pending.popleft()
                job.state = State.RUNNING
            result, error, uncertain = None, None, False
            try:
                # The adapter must return only after the native model operation has exited.
                result = self.model.generate(job.request, job.stop)
            except LlmError as exc:
                error = exc
            except ExecutionError as exc:
                error = failure(exc.code, exc.message, exc.retryable)
            except ExecutionUncertain:
                uncertain = True
            except Exception:
                error = failure("execution_failed", "Model execution failed.")
            finally:
                with self.condition:
                    if uncertain:
                        # Fail closed. The runtime exits; no terminal cancellation is claimed.
                        self.closing = True
                        job.state = State.CANCELLING
                    elif job.cancel_code:
                        self._finish(
                            job,
                            State.CANCELLED,
                            failure(job.cancel_code, "Generation was stopped."),
                        )
                    else:
                        self._finish(job, State.FAILED if error else State.SUCCEEDED, error, result)
                    if not uncertain:
                        self.active = None
                    self.condition.notify_all()
            if uncertain:
                return

    def begin_close(self):
        with self.condition:
            self.closing = True
            for job in list(self.pending):
                self._cancel(job, "shutdown")
            if self.active:
                self._cancel(self.active, "shutdown")
            self.condition.notify_all()

    def close(self, timeout=20):
        self.begin_close()
        self.worker.join(timeout)
        if self.worker.is_alive():
            raise RuntimeError("Generation did not exit during shutdown")
