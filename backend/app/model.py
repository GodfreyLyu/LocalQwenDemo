"""Compatibility exports for earlier model/evaluation scripts.

Implementation lives in app.inference; new callers and test patches use that package.
"""

from app.inference.model import (
    INVALID_REVIEW_MESSAGE,
    REQUIRED_SECTIONS,
    REVIEW_GENERATION_PARAMETERS,
    SECTION_SPECS,
    SECTION_TITLES,
    STARTUP_GENERATION_SEED,
    SYSTEM_PROMPT,
    ReviewModel,
    TransformersModel,
    allocate_section_token_limits,
    build_prompt_messages,
    build_section_request,
    derive_generation_seed,
    download_model_snapshot,
    logger,
    normalize_section_body,
    snapshot_has_model_weights,
    trim_capped_section_tail,
    validate_review_output,
)

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
    "logger",
]
