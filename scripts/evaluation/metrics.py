import logging
import resource
import sys

from app.inference.generation import allocate_section_token_limits  # noqa: E402
from app.inference.review_output import REQUIRED_SECTIONS  # noqa: E402


def peak_rss_mib():
    scale = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale, 2)


class MetricCapture(logging.Handler):
    """Allow only typed production metric fields; never format log messages or extras."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        if record.msg != "model_generation_finished":
            return
        safe = {}
        for key in (
            "duration_ms",
            "generated_tokens",
            "output_token_limit",
            "worker_intraop_threads",
            "worker_interop_threads",
        ):
            value = getattr(record, key, None)
            if type(value) is int and value >= 0:
                safe[key] = value
        for key in ("output_limit_reached",):
            value = getattr(record, key, None)
            if type(value) is bool:
                safe[key] = value
        for key in (
            "section_generated_tokens",
            "section_input_tokens",
            "section_prepare_ms",
            "section_generation_ms",
            "section_first_token_ms",
            "section_limits_reached",
            "section_trailing_fragments_removed",
        ):
            value = getattr(record, key, None)
            if not isinstance(value, dict) or set(value) != set(REQUIRED_SECTIONS):
                continue
            boolean = key in ("section_limits_reached", "section_trailing_fragments_removed")
            nullable = key in (
                "section_input_tokens",
                "section_generation_ms",
                "section_first_token_ms",
            )
            if all(
                (
                    type(v) is bool
                    if boolean
                    else (type(v) is int and v >= 0) or (nullable and v is None)
                )
                for v in value.values()
            ):
                safe[key] = value.copy()
        self.records.append(safe)


def metrics_valid(metrics, settings):
    limits = allocate_section_token_limits(settings.model_max_output_tokens)
    tokens = metrics.get("section_generated_tokens", {})
    return (
        set(tokens) == set(limits)
        and all(type(tokens[k]) is int and 0 < tokens[k] <= limits[k] for k in limits)
        and metrics.get("generated_tokens") == sum(tokens.values())
        and metrics.get("output_token_limit") == settings.model_max_output_tokens
        and all(
            0
            < (metrics.get("section_input_tokens", {}).get(k) or 0)
            <= settings.model_max_input_tokens
            for k in limits
        )
        and all(
            metrics.get("section_limits_reached", {}).get(k) == (tokens[k] >= limits[k])
            for k in limits
        )
        and metrics.get("output_limit_reached")
        == (sum(tokens.values()) >= settings.model_max_output_tokens)
        and all(
            metrics.get(name, {}).get(k) is not None
            for k in limits
            for name in (
                "section_input_tokens",
                "section_generation_ms",
                "section_first_token_ms",
            )
        )
        and all(
            name in metrics
            for name in (
                "duration_ms",
                "section_limits_reached",
                "section_trailing_fragments_removed",
                "output_limit_reached",
            )
        )
    )
