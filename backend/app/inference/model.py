"""Backend-only model loading, tokenization and serial CPU inference."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from torch import Tensor
    from transformers import BatchEncoding, StoppingCriteriaList

from app.config import Settings
from app.errors import AppError
from app.inference.generation import (
    REVIEW_GENERATION_PARAMETERS,
    STARTUP_GENERATION_SEED,
    GenerationContext,
    GenerationMetrics,
    SectionMetrics,
    allocate_section_token_limits,
    check_deadline,
    derive_generation_seed,
    stopping_criteria,
)
from app.inference.model_cache import ModelCacheIncompleteError, validate_model_snapshot
from app.inference.prompts import (
    SECTION_SPECS,
    SECTION_TITLES,
    SYSTEM_PROMPT,
    build_prompt_messages,
    build_section_request,
)
from app.inference.review_output import (
    INVALID_REVIEW_MESSAGE,
    REQUIRED_SECTIONS,
    normalize_section_body,
    trim_capped_section_tail,
    validate_review_output,
)
from app.startup import failure_fields, startup_stage

# Shared helpers also support the explicit app.model compatibility exports.
__all__ = [
    "ReviewModel",
    "TransformersModel",
    "REVIEW_GENERATION_PARAMETERS",
    "STARTUP_GENERATION_SEED",
    "SECTION_SPECS",
    "SECTION_TITLES",
    "SYSTEM_PROMPT",
    "REQUIRED_SECTIONS",
    "INVALID_REVIEW_MESSAGE",
    "build_prompt_messages",
    "build_section_request",
    "allocate_section_token_limits",
    "derive_generation_seed",
    "normalize_section_body",
    "trim_capped_section_tail",
    "validate_review_output",
    "snapshot_has_model_weights",
    "download_model_snapshot",
]

logger = logging.getLogger("review")


def snapshot_has_model_weights(path: str) -> bool:
    """Accept a complete single-file or indexed sharded safetensors snapshot."""

    try:
        validate_model_snapshot(path)
    except ModelCacheIncompleteError:
        return False
    return True


def download_model_snapshot(
    settings: Settings,
    snapshot_download: Callable[..., str],
    local_entry_not_found: type[Exception],
) -> str:
    """Reuse complete local weights; make one repair attempt and validate its result."""
    args = dict(
        repo_id=settings.model_id,
        revision=settings.model_revision,
        cache_dir=str(settings.hf_home / "hub"),
        allow_patterns=["*.json", "*.safetensors", "*.txt"],
    )
    with startup_stage("cache_lookup"):
        try:
            path = snapshot_download(**args, local_files_only=True)
        except local_entry_not_found:
            path = None
        try:
            if path is None:
                raise ModelCacheIncompleteError("missing_snapshot")
            validate_model_snapshot(path)
        except ModelCacheIncompleteError as exc:
            logger.warning(
                "model_cache_incomplete", extra={"stage": "cache_lookup", **failure_fields(exc)}
            )
        else:
            return path
    with startup_stage("model_download"):
        path = snapshot_download(**args)
    with startup_stage("cache_validation"):
        validate_model_snapshot(path)
    return path


class ReviewModel(Protocol):
    def load(self) -> None: ...
    def count_tokens(self, source: str, language: str) -> int: ...
    def review(self, source: str, language: str, stop: threading.Event) -> str: ...


class TransformersModel:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load(self) -> None:
        os.environ["HF_HOME"] = str(self.settings.hf_home)
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
        with startup_stage("model_dependencies"):
            import torch
            from huggingface_hub import snapshot_download
            from huggingface_hub.errors import LocalEntryNotFoundError
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self.torch = torch
            torch.set_num_threads(self.settings.model_cpu_threads)
            self.settings.hf_home.mkdir(parents=True, exist_ok=True)
        path = download_model_snapshot(self.settings, snapshot_download, LocalEntryNotFoundError)
        with startup_stage("tokenizer_load"):
            self.tokenizer = AutoTokenizer.from_pretrained(
                path, local_files_only=True, trust_remote_code=False
            )
        with startup_stage("weights_load"):
            self.model = AutoModelForCausalLM.from_pretrained(
                path,
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
                torch_dtype=getattr(torch, self.settings.model_dtype),
            )
        with startup_stage("cpu_placement"):
            self.model = self.model.to("cpu")
            self.model.eval()
        with startup_stage("startup_generation"):
            self.validate_startup_generation()

    def generate_with_seed(
        self,
        inputs: Mapping[str, Tensor],
        *,
        max_new_tokens: int,
        seed: int,
        stopping_criteria: StoppingCriteriaList | None = None,
    ) -> Tensor:
        generation_arguments = {
            **inputs,
            **REVIEW_GENERATION_PARAMETERS,
            "max_new_tokens": max_new_tokens,
            "use_cache": True,
            "pad_token_id": self.tokenizer.pad_token_id,
        }
        if stopping_criteria is not None:
            generation_arguments["stopping_criteria"] = stopping_criteria
        with self.torch.random.fork_rng(devices=[]):
            self.torch.random.default_generator.manual_seed(seed)
            return self.model.generate(**generation_arguments)

    def validate_startup_generation(self) -> None:
        sample = self.tokenizer("Review code.", return_tensors="pt")
        with self.torch.inference_mode():
            output = self.generate_with_seed(
                sample,
                max_new_tokens=1,
                seed=STARTUP_GENERATION_SEED,
            )
        if output.shape[-1] <= sample["input_ids"].shape[-1]:
            raise RuntimeError("Model startup validation failed.")

    def prompt(self, source: str, language: str, section: str) -> str:
        title = SECTION_TITLES[section]
        messages = build_prompt_messages(source, language, section)
        if self.tokenizer.chat_template:
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        plain_messages = "\n\n".join(
            f"{message['role'].title()} instructions:\n{message['content']}" for message in messages
        )
        return plain_messages + f"\n\n{title} body:\n"

    def count_tokens(self, source: str, language: str) -> int:
        return max(
            len(
                self.tokenizer.encode(
                    self.prompt(source, language, section), add_special_tokens=False
                )
            )
            for section, _, _ in SECTION_SPECS
        )

    def _prepare_section(
        self, source: str, language: str, section: str, metrics: SectionMetrics
    ) -> BatchEncoding:
        started_at = time.monotonic()
        inputs = self.tokenizer(
            self.prompt(source, language, section),
            return_tensors="pt",
            add_special_tokens=False,
        )
        metrics.prepare_ms = max(0, round((time.monotonic() - started_at) * 1000))
        metrics.input_tokens = int(inputs["input_ids"].shape[-1])
        if metrics.input_tokens > self.settings.model_max_input_tokens:
            raise AppError("token_limit", "Source exceeds the model input token limit.", 422)
        return inputs

    def _generate_section(
        self,
        inputs: Mapping[str, Tensor],
        *,
        limit: int,
        seed: int,
        metrics: SectionMetrics,
        context: GenerationContext,
    ) -> str:
        started_at = time.monotonic()
        try:
            with self.torch.inference_mode():
                output = self.generate_with_seed(
                    inputs,
                    max_new_tokens=limit,
                    seed=seed,
                    stopping_criteria=stopping_criteria(
                        context.stop, context.deadline, started_at, metrics
                    ),
                )
        finally:
            finished_at = time.monotonic()
            elapsed = max(0, finished_at - started_at)
            context.metrics.duration_seconds += elapsed
            metrics.generation_ms = round(elapsed * 1000)
        input_tokens = int(inputs["input_ids"].shape[-1])
        metrics.generated_tokens = int(output.shape[-1] - input_tokens)
        metrics.limit_reached = metrics.generated_tokens >= limit
        check_deadline(context.stop, context.deadline, now=finished_at)
        return self.tokenizer.decode(output[0][input_tokens:], skip_special_tokens=True)

    def _review_sections(self, source: str, language: str, context: GenerationContext) -> str:
        limits = allocate_section_token_limits(self.settings.model_max_output_tokens)
        bodies = []
        for section, title, _ in SECTION_SPECS:
            check_deadline(context.stop, context.deadline)
            metrics = context.metrics.sections[section]
            inputs = self._prepare_section(source, language, section, metrics)
            decoded = self._generate_section(
                inputs,
                limit=limits[section],
                seed=derive_generation_seed(
                    self.settings.model_revision, language, section, source
                ),
                metrics=metrics,
                context=context,
            )
            body = normalize_section_body(section, decoded)
            body, metrics.trailing_fragment_removed = trim_capped_section_tail(
                body, metrics.limit_reached
            )
            bodies.append(f"## {title}\n{body}")
        return "\n\n".join(bodies)

    def review(self, source: str, language: str, stop: threading.Event) -> str:
        deadline = time.monotonic() + self.settings.inference_timeout_seconds
        metrics = GenerationMetrics(
            output_token_limit=self.settings.model_max_output_tokens,
            worker_intraop_threads=self.torch.get_num_threads(),
            worker_interop_threads=self.torch.get_num_interop_threads(),
            sections={section: SectionMetrics() for section, _, _ in SECTION_SPECS},
        )
        context = GenerationContext(stop=stop, deadline=deadline, metrics=metrics)
        try:
            result = self._review_sections(source, language, context)
        finally:
            if any(section.generation_ms is not None for section in metrics.sections.values()):
                logger.info("model_generation_finished", extra=metrics.fields())
        return validate_review_output(result, source)
