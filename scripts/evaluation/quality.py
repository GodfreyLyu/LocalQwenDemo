import json
import re
import threading
import time
from datetime import UTC, datetime

from app.errors import AppError, ValidationReason  # noqa: E402
from app.inference.review_output import validate_review_output  # noqa: E402
from evaluation.config import MANUAL_CHECKS
from evaluation.metrics import metrics_valid, peak_rss_mib


def pending_case(case_id):
    return {
        "id": case_id,
        "status": "not_run",
        "error_code": None,
        "checks": {
            "production_contract": "not_run",
            "runtime_budget": "not_run",
            "metrics_contract": "not_run",
            **dict.fromkeys(MANUAL_CHECKS, "not_run"),
        },
        "metrics": {},
        "hints": {},
        "manual_review": {"method": "not_run"},
    }


def finite_hints(result, case):
    """Signals for review, never an automatic semantic pass or an assertion of execution."""
    lowered = result.casefold()
    bodies = re.split(r"(?im)^## (?:Summary|Findings|Suggestions)\s*$", result)[1:]
    return {
        "concept_groups_present": [
            any(term.casefold() in lowered for term in group) for group in case["concept_groups"]
        ],
        "injection_marker_mentioned": bool(
            case["injection_marker"] and case["injection_marker"].casefold() in lowered
        ),
        "possible_execution_claim": bool(
            re.search(
                r"\b(?:i|we)\s+(?:(?:have|actually|successfully)\s+)*(?:ran|run|executed|compiled|tested)\b",
                lowered,
            )
        ),
        "terminal_punctuation_present": [
            bool(re.search(r"[.!?][\s\"'`*)\]]*$", b)) for b in bodies
        ],
    }


def manual_review(result, case, terminal, answers):
    """Explicit synthetic-only private terminal view; retain verdicts, never free-form text."""
    # JSON string escaping prevents generated ANSI/control characters from controlling the terminal.
    terminal.write("\nSynthetic evaluation output (not saved; do not record this terminal):\n")
    terminal.write(json.dumps(result, ensure_ascii=True) + "\n")
    terminal.write("Expected assessment: " + case["expectation"] + "\n")
    verdicts = {}
    for name in MANUAL_CHECKS:
        terminal.write(f"Confirm {name}: y=passed, n=failed, anything else=pending: ")
        terminal.flush()
        answer = answers.readline().strip().lower()
        verdicts[name] = {"y": "passed", "n": "failed"}.get(answer, "needs_manual_review")
    return verdicts


def evaluate_case(model, settings, case, capture, reviewer=None, clock=time.monotonic):
    """Reuse production guards; only complete contract/metrics AND manual judgments can pass."""
    row = pending_case(case["id"])
    started = clock()
    before = len(capture.records)
    try:
        result = model.review(case["source"], case["language"], threading.Event())
        elapsed = clock() - started
        row["checks"]["runtime_budget"] = (
            "passed" if elapsed < settings.inference_timeout_seconds else "failed"
        )
        validate_review_output(result, case["source"])
        row["checks"]["production_contract"] = "passed"
        row["hints"] = finite_hints(result, case)
        row["checks"].update(dict.fromkeys(MANUAL_CHECKS, "needs_manual_review"))
        if reviewer is not None and row["checks"]["runtime_budget"] == "passed":
            decisions = reviewer(result, case)
            for key in MANUAL_CHECKS:
                if decisions.get(key) in {"passed", "failed", "needs_manual_review"}:
                    row["checks"][key] = decisions[key]
            row["manual_review"] = {
                "method": "private_terminal",
                "at": datetime.now(UTC).isoformat(),
            }
        del result
    except AppError as exc:
        row["error_code"] = (
            exc.code
            if exc.code in {"inference_timeout", "invalid_model_response", "token_limit"}
            else "model_failure"
        )
        row["checks"]["production_contract"] = "failed"
        if exc.code == "inference_timeout":
            row["checks"]["runtime_budget"] = "failed"
        row["validation_reason"] = (
            exc.validation_reason.value
            if isinstance(exc.validation_reason, ValidationReason)
            else None
        )
    except TimeoutError:
        row["error_code"] = "inference_timeout"
        row["checks"]["runtime_budget"] = "failed"
        row["checks"]["production_contract"] = "failed"
    except Exception:
        row["error_code"] = "model_failure"
        row["checks"]["production_contract"] = "failed"
    row["metrics"] = capture.records[-1].copy() if len(capture.records) == before + 1 else {}
    row["checks"]["metrics_contract"] = (
        "passed" if metrics_valid(row["metrics"], settings) else "failed"
    )
    # Human reading time is deliberately excluded from inference timing.
    row["metrics"]["review_wall_seconds"] = round(locals().get("elapsed", clock() - started), 3)
    row["metrics"]["peak_process_rss_mib"] = peak_rss_mib()
    row["status"] = aggregate(row["checks"].values())
    if row["checks"]["runtime_budget"] == "failed" and row["error_code"] is None:
        row["error_code"] = "inference_timeout"
    return row


def aggregate(statuses):
    states = set(statuses)
    if "failed" in states:
        return "failed"
    if states == {"passed"}:
        return "passed"
    if "needs_manual_review" in states:
        return "needs_manual_review"
    return "not_run"
