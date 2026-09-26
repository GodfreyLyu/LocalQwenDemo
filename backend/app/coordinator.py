import asyncio
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum

from app.config import Settings
from app.domain import ReviewRecord
from app.errors import AppError
from app.inference.identity import model_identity
from app.inference.model import ReviewModel
from app.persistence.storage import Store
from app.persistence.users import UserStore
from app.persistence.users_startup import check_users_storage
from app.startup import failure_fields

logger = logging.getLogger("review")


def milliseconds_since(started_at: float, *, now: float | None = None) -> int:
    return max(0, round(((time.time() if now is None else now) - started_at) * 1000))


class CoordinatorState(StrEnum):
    LOADING = "model_loading"
    READY = "ready"
    DRAINING = "inference_draining"
    STUCK = "inference_stuck"
    FAILED = "startup_or_storage_failure"


class Coordinator:
    def __init__(
        self, store: Store, users: UserStore, model: ReviewModel, settings: Settings
    ) -> None:
        self.store, self.users, self.model, self.settings = store, users, model, settings
        self._state = CoordinatorState.LOADING
        self._closing = False
        self._shutdown = asyncio.Event()
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")

    @property
    def state(self) -> CoordinatorState:
        return self._state

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def ready(self) -> bool:
        return self.state is CoordinatorState.READY and not self.closing

    @property
    def live(self) -> bool:
        return self.state not in {CoordinatorState.FAILED, CoordinatorState.STUCK}

    def _transition(self, state: CoordinatorState) -> None:
        self._state = state
        if state is CoordinatorState.STUCK:
            self._closing = True

    def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        stage = "storage_initialize"
        try:
            await asyncio.to_thread(self.store.initialize)
            stage = "users_storage"
            if not await check_users_storage(self.users.ping, self._shutdown):
                return
            stage = "queue_recovery"
            await asyncio.to_thread(
                self.store.recover, self.settings.max_retries, self.settings.queue_capacity
            )
            stage = "model_load"
            await asyncio.get_running_loop().run_in_executor(self.executor, self.model.load)
            stage = "post_model_storage"
            await asyncio.to_thread(self.store.ping)
            self._transition(CoordinatorState.READY)
            logger.info("model_ready")
            stage = "queue_processing"
            while not self.closing:
                job = await asyncio.to_thread(self.store.claim)
                if job is None:
                    await asyncio.sleep(0.1)
                    continue
                await self.process(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._transition(CoordinatorState.FAILED)
            logger.error("coordinator_failed", extra={"stage": stage, **failure_fields(exc)})

    async def process(self, job: ReviewRecord) -> None:
        self.stop.clear()
        await self.log_started(job)
        future = asyncio.get_running_loop().run_in_executor(
            self.executor,
            self.model.review,
            job["source_code"],
            job["language"],
            self.stop,
        )
        result, error, timed_out = None, None, False
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future), self.settings.inference_timeout_seconds
            )
            if not result or not result.strip():
                raise AppError(
                    "empty_model_response", "The model returned no review. Please retry."
                )
        except TimeoutError:
            self.stop.set()
            self._transition(CoordinatorState.DRAINING)
            timed_out = True
            error = AppError("inference_timeout", "Review timed out. Try a shorter submission.")
        except AppError as exc:
            error = exc
        except Exception:
            error = AppError("inference_failed", "The model could not complete this review.")
        await self.finish(job, result, error)
        if timed_out:
            await self.drain(future, job["review_id"])

    async def log_started(self, job: ReviewRecord) -> None:
        queue_wait_ms = milliseconds_since(job["created_at"])
        start_fields: dict[str, str | int] = {
            "review_id": job["review_id"],
            "outcome": "accepted",
            "error_code": "none",
            "queue_wait_ms": queue_wait_ms,
        }
        try:
            start_fields["queue_depth"] = await asyncio.to_thread(self.store.queue_depth)
        except sqlite3.Error:
            # Observability must not interrupt a review after it has been claimed.
            pass
        logger.info(
            "review_started",
            extra=start_fields,
        )

    async def finish(self, job: ReviewRecord, result: str | None, error: AppError | None) -> None:
        identity = model_identity(self.model, self.settings)
        await asyncio.to_thread(
            self.store.finish,
            job["review_id"],
            result=result,
            error=error,
            model_id=identity["model_id"] if not error else None,
            revision=identity["model_revision"] if not error else None,
        )
        log_fields: dict[str, str | int] = {
            "review_id": job["review_id"],
            "outcome": error.code if error else "completed",
            "error_code": error.code if error else "none",
            "duration_ms": milliseconds_since(job["created_at"]),
        }
        if error and error.validation_reason is not None:
            log_fields["validation_reason"] = error.validation_reason.value
        logger.info(
            "review_finished",
            extra=log_fields,
        )

    async def drain(self, future: asyncio.Future[str], review_id: str) -> None:
        # A timed-out Python thread cannot be killed. Never start another inference over it.
        try:
            await asyncio.wait_for(asyncio.shield(future), self.settings.inference_drain_seconds)
        except TimeoutError:
            self._transition(CoordinatorState.STUCK)
            logger.error(
                "inference_stuck",
                extra={
                    "review_id": review_id,
                    "outcome": "inference_stuck",
                    "error_code": "inference_stuck",
                },
            )
            future.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
            return
        except Exception:
            pass
        self._transition(CoordinatorState.READY)

    async def close(self) -> None:
        self._closing = True
        self._shutdown.set()
        self.stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(self.task), self.settings.shutdown_grace_seconds)
        except (TimeoutError, asyncio.CancelledError):
            self.task.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
