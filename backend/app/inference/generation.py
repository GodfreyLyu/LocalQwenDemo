"""Fixed sampling policy, shared cancellation and content-free generation metrics."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transformers import StoppingCriteriaList

from app.errors import AppError
from app.inference.prompts import SECTION_SPECS


@dataclass
class SectionMetrics:
    generated_tokens: int = 0
    limit_reached: bool = False
    trailing_fragment_removed: bool = False
    # None means a stage was not observed, rather than completed in zero time.
    prepare_ms: int | None = None
    generation_ms: int | None = None
    first_token_ms: int | None = None
    input_tokens: int | None = None


@dataclass
class GenerationMetrics:
    output_token_limit: int
    worker_intraop_threads: int
    worker_interop_threads: int
    sections: dict[str, SectionMetrics] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def fields(self) -> dict[str, object]:
        generated_tokens = sum(section.generated_tokens for section in self.sections.values())
        fields: dict[str, object] = {
            "duration_ms": max(0, round(self.duration_seconds * 1000)),
            "generated_tokens": generated_tokens,
            "output_limit_reached": generated_tokens >= self.output_token_limit,
            "output_token_limit": self.output_token_limit,
            "worker_intraop_threads": self.worker_intraop_threads,
            "worker_interop_threads": self.worker_interop_threads,
        }
        for key, attribute in (
            ("section_generated_tokens", "generated_tokens"),
            ("section_prepare_ms", "prepare_ms"),
            ("section_generation_ms", "generation_ms"),
            ("section_first_token_ms", "first_token_ms"),
            ("section_input_tokens", "input_tokens"),
            ("section_limits_reached", "limit_reached"),
            ("section_trailing_fragments_removed", "trailing_fragment_removed"),
        ):
            fields[key] = {
                name: getattr(metrics, attribute) for name, metrics in self.sections.items()
            }
        return fields


def check_deadline(stop: threading.Event, deadline: float, *, now: float | None = None) -> None:
    if stop.is_set() or (time.monotonic() if now is None else now) >= deadline:
        raise AppError("inference_timeout", "Review timed out. Try a shorter submission.", 504)


def stopping_criteria(
    stop: threading.Event, deadline: float, started_at: float, metrics: SectionMetrics
) -> StoppingCriteriaList:
    # Keep transformers optional until actual inference, just as model loading does.
    from transformers import StoppingCriteria, StoppingCriteriaList

    class Deadline(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs):
            observed_at = time.monotonic()
            if metrics.first_token_ms is None:
                # Includes prefill, first-token sampling and callback overhead.
                metrics.first_token_ms = max(0, round((observed_at - started_at) * 1000))
            return stop.is_set() or observed_at >= deadline

    return StoppingCriteriaList([Deadline()])


REVIEW_GENERATION_PARAMETERS = {
    "do_sample": True,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
}
STARTUP_GENERATION_SEED = 0


SECTION_TOKEN_WEIGHTS = {"summary": 9, "findings": 22, "suggestions": 17}


def derive_generation_seed(model_revision: str, language: str, section: str, source: str) -> int:
    """Derive a stable nonnegative 63-bit seed without retaining source-derived data."""

    digest = hashlib.sha256(b"local-llm-code-review-generation-seed-v1")
    for component in (model_revision, language, section, source):
        encoded = component.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return int.from_bytes(digest.digest()[:8], "big") & ((1 << 63) - 1)


def allocate_section_token_limits(total_limit: int) -> dict[str, int]:
    """Split one review-wide output budget across the three fixed sections."""

    if total_limit < len(SECTION_SPECS):
        raise ValueError("The review output token limit must allow at least one token per section.")
    sections = [section for section, _, _ in SECTION_SPECS]
    remaining = total_limit - len(sections)
    total_weight = sum(SECTION_TOKEN_WEIGHTS.values())
    limits = {}
    remainders = []
    for position, section in enumerate(sections):
        quotient, remainder = divmod(remaining * SECTION_TOKEN_WEIGHTS[section], total_weight)
        limits[section] = 1 + quotient
        remainders.append((remainder, -position, section))
    for _, _, section in sorted(remainders, reverse=True)[: total_limit - sum(limits.values())]:
        limits[section] += 1
    return limits


@dataclass(frozen=True)
class GenerationContext:
    stop: threading.Event
    deadline: float
    metrics: GenerationMetrics
