"""Bounded startup diagnostics: never format exceptions, URLs or model/user content."""

import logging
import re
from contextlib import contextmanager

from app.inference.model_cache import ModelCacheIncompleteError

logger = logging.getLogger("review")
STAGES = frozenset(
    {
        "storage_initialize",
        "users_storage",
        "queue_recovery",
        "model_load",
        "model_dependencies",
        "cache_lookup",
        "model_download",
        "cache_validation",
        "tokenizer_load",
        "weights_load",
        "cpu_placement",
        "startup_generation",
        "post_model_storage",
        "queue_processing",
    }
)
CACHE_REASONS = frozenset(
    {
        "missing_snapshot",
        "unreadable_index",
        "invalid_index",
        "missing_shard",
        "unreadable_shard",
        "invalid_safetensors",
        "index_tensor_mismatch",
    }
)


def safe_diagnostic_fields(fields):
    result = {}
    if fields.get("stage") in STAGES:
        result["stage"] = fields["stage"]
    if fields.get("cache_reason") in CACHE_REASONS:
        result["cache_reason"] = fields["cache_reason"]
    name = fields.get("exception_type")
    if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        result["exception_type"] = name
    for key, lower, upper in (
        ("errno", 1, 4095),
        ("http_status", 100, 599),
        ("startup_attempt", 0, 1000),
        ("startup_wait_ms", 0, 10000),
        ("startup_elapsed_ms", 0, 3600000),
    ):
        value = fields.get(key)
        if type(value) is int and lower <= value <= upper:
            result[key] = value
    return result


def failure_fields(exc):
    fields = safe_diagnostic_fields(
        {
            "exception_type": type(exc).__name__,
            "errno": getattr(exc, "errno", None),
            "http_status": getattr(getattr(exc, "response", None), "status_code", None),
            "cache_reason": getattr(exc, "cache_reason", None),
        }
    )
    fields["error_code"] = (
        "model_cache_incomplete"
        if isinstance(exc, ModelCacheIncompleteError)
        else "startup_or_storage_failure"
    )
    return fields


@contextmanager
def startup_stage(stage):
    assert stage in STAGES
    logger.info("startup_stage_started", extra={"stage": stage})
    try:
        yield
    except Exception as exc:
        logger.error("startup_stage_failed", extra={"stage": stage, **failure_fields(exc)})
        raise
    else:
        logger.info("startup_stage_completed", extra={"stage": stage})
