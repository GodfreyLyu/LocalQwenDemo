import asyncio
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.errors import AppError
from app.startup import failure_fields

logger = logging.getLogger("review")


def milliseconds_since(started_at, *, now=None):
    return max(0, round(((time.time() if now is None else now) - started_at) * 1000))


class Coordinator:
    def __init__(self, store, users, model, settings):
        self.store, self.users, self.model, self.settings = store, users, model, settings
        self.ready = False
        self.live = True
        self.state = "model_loading"
        self.closing = False
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def run(self):
        stage = "storage_initialize"
        try:
            await asyncio.to_thread(self.store.initialize)
            stage = "users_storage"
            await asyncio.to_thread(self.users.ping)
            stage = "queue_recovery"
            await asyncio.to_thread(
                self.store.recover, self.settings.max_retries, self.settings.queue_capacity
            )
            stage = "model_load"
            await asyncio.get_running_loop().run_in_executor(self.executor, self.model.load)
            stage = "post_model_storage"
            await asyncio.to_thread(self.store.ping)
            self.ready, self.state = True, "ready"
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
            self.ready, self.live, self.state = False, False, "startup_or_storage_failure"
            logger.error("coordinator_failed", extra={"stage": stage, **failure_fields(exc)})

    async def process(self, job):
        self.stop.clear()
        queue_wait_ms = milliseconds_since(job["created_at"])
        start_fields = {
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
            self.ready, self.state, timed_out = False, "inference_draining", True
            error = AppError("inference_timeout", "Review timed out. Try a shorter submission.")
        except AppError as exc:
            error = exc
        except Exception:
            error = AppError("inference_failed", "The model could not complete this review.")
        await asyncio.to_thread(
            self.store.finish,
            job["review_id"],
            result=result,
            error=error,
            model_id=self.settings.model_id if not error else None,
            revision=self.settings.model_revision if not error else None,
        )
        log_fields = {
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
        if timed_out:
            # A timed-out Python thread cannot be killed. Never start another inference over it.
            try:
                await asyncio.wait_for(
                    asyncio.shield(future), self.settings.inference_drain_seconds
                )
            except TimeoutError:
                self.live, self.state, self.closing = False, "inference_stuck", True
                logger.error(
                    "inference_stuck",
                    extra={
                        "review_id": job["review_id"],
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
            self.ready, self.state = True, "ready"

    async def close(self):
        self.closing, self.ready = True, False
        self.stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(self.task), self.settings.shutdown_grace_seconds)
        except (TimeoutError, asyncio.CancelledError):
            self.task.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
