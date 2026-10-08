import json
import logging

from tooling_paths import ROOT

from app.config import Settings  # noqa: E402

logger = logging.getLogger("review")
TOOL_VERSION = "3.1.0"
FIXTURES = ROOT / "scripts/evaluation/fixtures-v1.json"
OUTPUT_ROOT = ROOT / ".local/model-evaluations"
CASE_IDS = ("hello_world", "average", "square", "first_item", "sql_injection", "prompt_injection")
DEPENDENCIES = ("httpx",)
MANUAL_CHECKS = (
    "semantic_correctness",
    "no_fabricated_findings",
    "injection_resistance",
    "no_execution_claims",
    "complete_endings",
)
LOAD_TIMEOUT_SECONDS = 600
DRAIN_SECONDS = 30


class EvaluationError(Exception):
    """A fixed public code, never an arbitrary third-party exception message."""


def settings_for(
    *,
    ollama_base_url="http://localhost:11434",
    ollama_model_digest=None,
    model_max_output_tokens=384,
    inference_timeout_seconds=300,
    **model_overrides,
):
    """Use production validation while isolating every setting from .env and host overrides."""
    defaults = {name: field.default for name, field in Settings.model_fields.items()}
    defaults.update(
        signing_secret="synthetic-evaluation-only-" * 2,
        dynamodb_endpoint_url="http://127.0.0.1:8001",
        ollama_base_url=ollama_base_url,
        ollama_model_digest=ollama_model_digest,
        model_inference_concurrency=1,
        model_max_input_tokens=2048,
        model_max_output_tokens=model_max_output_tokens,
        inference_timeout_seconds=inference_timeout_seconds,
    )
    defaults.update(model_overrides)
    return Settings(_env_file=None, **defaults)


def load_fixtures():
    """Only the checked-in synthetic suite is accepted; no user-source/file input option."""
    value = json.loads(FIXTURES.read_text())
    if (
        value["schema_version"] != 1
        or value["suite_version"] != "synthetic-review-v1"
        or tuple(c["id"] for c in value["cases"]) != CASE_IDS
    ):
        raise EvaluationError("fixture_contract")
    for case in value["cases"]:
        if case["language"] != "python" or not isinstance(case["source"], str):
            raise EvaluationError("fixture_contract")
        if not case["source"] or len(case["source"]) > 12000:
            raise EvaluationError("fixture_contract")
        if not isinstance(case["expectation"], str) or not case["expectation"]:
            raise EvaluationError("fixture_contract")
        groups = case["concept_groups"]
        if not isinstance(groups, list) or any(
            not isinstance(g, list) or not g or any(not isinstance(x, str) or not x for x in g)
            for g in groups
        ):
            raise EvaluationError("fixture_contract")
        if case["injection_marker"] is not None and not isinstance(case["injection_marker"], str):
            raise EvaluationError("fixture_contract")
    return value
