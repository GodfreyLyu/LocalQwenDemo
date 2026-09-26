"""Opt-in offline evaluation of the production model on versioned synthetic fixtures.

The parent writes content-free reports and supervises one serial model worker.
The worker reuses production inference and metrics, blocks network/downloads, and
suppresses library/native output. Only explicit private terminal review can show
synthetic model output; no source, prompt, body or token IDs enter reports.
"""

import argparse
import hashlib
import importlib.metadata
import json
import logging
import multiprocessing
import os
import platform
import re
import resource
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import MODEL_REVISION, Settings  # noqa: E402
from app.errors import AppError, ValidationReason  # noqa: E402
from app.inference.model import (  # noqa: E402
    REQUIRED_SECTIONS,
    REVIEW_GENERATION_PARAMETERS,
    TransformersModel,
    allocate_section_token_limits,
    logger,
    validate_review_output,
)
from app.inference.model_cache import validate_model_snapshot  # noqa: E402

TOOL_VERSION = "1.0.0"
FIXTURES = ROOT / "scripts/evaluation/fixtures-v1.json"
CACHE_DEFAULT = ROOT / ".local/models/huggingface"
OUTPUT_ROOT = ROOT / ".local/model-evaluations"
CASE_IDS = ("hello_world", "average", "square", "first_item", "sql_injection", "prompt_injection")
DEPENDENCIES = ("torch", "transformers", "huggingface-hub", "safetensors", "numpy")
MANUAL_CHECKS = (
    "semantic_correctness",
    "no_fabricated_findings",
    "injection_resistance",
    "no_execution_claims",
    "complete_endings",
)
LOAD_TIMEOUT_SECONDS = 600
# Supervision bounds stuck native code; this does not extend the production inference deadline.
DRAIN_SECONDS = 30


class EvaluationError(Exception):
    """A fixed public code, never an arbitrary third-party exception message."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid evaluation arguments. Use --help.\n")


def settings_for(cache):
    """Use production validation while isolating every setting from .env and host overrides."""
    defaults = {name: field.default for name, field in Settings.model_fields.items()}
    defaults.update(
        signing_secret="synthetic-evaluation-only-" * 2,
        dynamodb_endpoint_url="http://127.0.0.1:8001",
        model_id="Qwen/Qwen3-1.7B",
        model_revision=MODEL_REVISION,
        model_dtype="bfloat16",
        model_cpu_threads=2,
        model_inference_concurrency=1,
        model_max_input_tokens=2048,
        model_max_output_tokens=384,
        inference_timeout_seconds=300,
        hf_home=cache,
    )
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


def digest_files(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def versions():
    result = {}
    for package in DEPENDENCIES:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def git_identity():
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, stderr=subprocess.DEVNULL
            )
        )
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise ValueError
        return {"revision": revision, "dirty": dirty}
    except (OSError, ValueError, subprocess.CalledProcessError):
        return {"revision": None, "dirty": None}


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


def build_report(settings, suite, selected, real, manual):
    now = datetime.now().astimezone()
    return {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "started_at": now.isoformat(),
        "timezone": str(now.tzinfo),
        "utc_offset": now.strftime("%z"),
        "finished_at": None,
        "git": git_identity(),
        "tool_sha256": digest_files([Path(__file__).resolve()]),
        "fixture_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        "fixture_version": suite["suite_version"],
        "baseline_kind": "new_synthetic_baseline_not_historical_reproduction",
        "scope": "full_suite" if selected == list(CASE_IDS) else "selected_case",
        "implementation_sha256": digest_files(
            [
                ROOT / f"backend/app/{name}.py"
                for name in (
                    "inference/model",
                    "config",
                    "inference/model_cache",
                    "errors",
                    "startup",
                )
            ]
        ),
        "dependency_locks_sha256": digest_files(
            [
                ROOT / f"backend/{name}"
                for name in (
                    "requirements.lock",
                    "requirements-model.lock",
                    "requirements-dev.lock",
                )
            ]
        ),
        "environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "dependencies": versions(),
        },
        "parameters": {
            "model_id": settings.model_id,
            "model_revision": settings.model_revision,
            "device": "cpu",
            "dtype": settings.model_dtype,
            "cpu_threads": settings.model_cpu_threads,
            "omp_num_threads": 2,
            "concurrency": 1,
            "max_input_tokens": settings.model_max_input_tokens,
            "max_output_tokens": settings.model_max_output_tokens,
            "section_limits": allocate_section_token_limits(settings.model_max_output_tokens),
            "inference_timeout_seconds": settings.inference_timeout_seconds,
            "load_watchdog_seconds": LOAD_TIMEOUT_SECONDS,
            "drain_watchdog_seconds": DRAIN_SECONDS,
            "generation": REVIEW_GENERATION_PARAMETERS.copy(),
            "enable_thinking": False,
            "trust_remote_code": False,
            "seed_policy": "production_stable_per_section",
            "offline": True,
            "downloads_allowed": False,
            "cache_scope": "project_default"
            if settings.hf_home == CACHE_DEFAULT
            else "explicit_local_path",
            "selected_cases": selected,
            "private_terminal_review": manual,
        },
        "mode": "real_model" if real else "plan_only",
        "status": "not_run",
        "load": {"status": "not_run", "seconds": None, "peak_process_rss_mib": None},
        "cases": [pending_case(case_id) for case_id in selected],
        "limitations": [
            "Synthetic baseline; not a reconstruction of the six historical inputs.",
            "Concept/claim/ending hints are not semantic proof; human judgments are required.",
            "No token cap reached does not prove complete sentences or complete reasoning.",
            "Process RSS is a lifetime high-water mark, not per-case/container memory.",
            "Identical settings do not guarantee cross-platform text or historical latency.",
            "No browser, service, Kubernetes, AWS or user-data acceptance is performed.",
        ],
    }


def write_report(directory, report):
    """Atomic updates are confined to a fresh private run; no historical directory is reused."""
    path = directory / "report.tmp"
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    path.replace(directory / "report.json")


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
                "section_prepare_ms",
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
        and metrics.get("worker_intraop_threads") == settings.model_cpu_threads
        and metrics.get("worker_interop_threads", 0) > 0
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
                "section_prepare_ms",
                "section_generation_ms",
                "section_first_token_ms",
            )
        )
        and all(
            name in metrics
            for name in (
                "duration_ms",
                "worker_interop_threads",
                "section_limits_reached",
                "section_trailing_fragments_removed",
                "output_limit_reached",
            )
        )
    )


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


@contextmanager
def offline_runtime(settings):
    """Force local Hub resolution and deny sockets even if a cache changes after preflight."""
    import socket

    fixed = {
        "HF_HOME": str(settings.hf_home),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_HUB_DISABLE_XET": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "2",
    }

    def denied(*args, **kwargs):
        raise EvaluationError("network_disabled")

    with (
        patch.dict(os.environ, fixed),
        patch.object(socket.socket, "connect", denied),
        patch.object(socket, "create_connection", denied),
    ):
        import huggingface_hub
        from huggingface_hub import constants

        original = huggingface_hub.snapshot_download

        def local_snapshot(*args, **kwargs):
            kwargs.update(local_files_only=True, token=False)
            return original(*args, **kwargs)

        with (
            patch.object(constants, "HF_HUB_OFFLINE", True),
            patch.object(huggingface_hub, "snapshot_download", local_snapshot),
        ):
            snapshot = local_snapshot(
                repo_id=settings.model_id,
                revision=settings.model_revision,
                cache_dir=str(settings.hf_home / "hub"),
            )
            validate_model_snapshot(snapshot)
            yield


def worker(connection, cache, cases, manual):
    """One disposable worker owns all model memory; parent can reap it if native code stalls."""
    # Redirect native file descriptors, not only Python streams; do not save suppressed output.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())
    capture = MetricCapture()
    logger.handlers[:] = [capture]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    settings = settings_for(Path(cache))
    started = time.monotonic()
    phase = "offline_cache_invalid"
    try:
        with offline_runtime(settings):
            phase = "model_load_failed"
            model = TransformersModel(settings)
            model.load()
            if (
                str(model.model.device) != "cpu"
                or model.model.training
                or str(model.model.dtype) != "torch.bfloat16"
            ):
                raise EvaluationError("runtime_contract")
            connection.send(
                (
                    "load",
                    {
                        "status": "passed",
                        "seconds": round(time.monotonic() - started, 3),
                        "peak_process_rss_mib": peak_rss_mib(),
                    },
                )
            )
            phase = "worker_failed"
            for index, case in enumerate(cases):
                connection.send(("stage", index, "inference"))

                def reviewer(result, current, case_index=index):
                    connection.send(("stage", case_index, "manual"))
                    with (
                        open("/dev/tty", encoding="utf-8") as answers,
                        open("/dev/tty", "w", encoding="utf-8") as terminal,
                    ):
                        return manual_review(result, current, terminal, answers)

                row = evaluate_case(model, settings, case, capture, reviewer if manual else None)
                connection.send(("case", index, row))
            connection.send(("done",))
    except Exception:
        connection.send(("failure", phase, round(time.monotonic() - started, 3), peak_rss_mib()))
    finally:
        connection.close()


def supervise(report, settings, cases, directory, manual, context=None):
    """Persist each sanitized result; a stalled/crashed worker never produces a passing run."""
    context = context or multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(child, str(settings.hf_home), cases, manual))
    process.start()
    child.close()
    deadline = time.monotonic() + LOAD_TIMEOUT_SECONDS
    active = None
    complete = False
    try:
        while True:
            if parent.poll(0.1):
                try:
                    event = parent.recv()
                except EOFError:
                    break
                if event[0] == "load":
                    report["load"] = event[1]
                elif event[0] == "stage":
                    active = event[1]
                    deadline = (
                        None
                        if event[2] == "manual"
                        else (time.monotonic() + settings.inference_timeout_seconds + DRAIN_SECONDS)
                    )
                elif event[0] == "case":
                    report["cases"][event[1]] = event[2]
                    active = None
                    deadline = time.monotonic() + DRAIN_SECONDS
                elif event[0] == "failure":
                    report["error_code"] = event[1]
                    report["load"] = {
                        "status": "failed",
                        "seconds": event[2],
                        "peak_process_rss_mib": event[3],
                    }
                    break
                elif event[0] == "done":
                    complete = True
                    break
                write_report(directory, report)
            if deadline is not None and time.monotonic() >= deadline:
                report["error_code"] = "worker_timeout"
                break
            if not process.is_alive() and not parent.poll():
                break
    except KeyboardInterrupt:
        report["error_code"] = "interrupted"
    finally:
        if not complete:
            report["error_code"] = report.get("error_code", "worker_failed")
            if active is not None:
                row = report["cases"][active]
                row["status"] = "failed"
                row["error_code"] = report["error_code"]
                row["checks"]["runtime_budget"] = "failed"
            elif report["load"]["status"] == "not_run":
                report["load"]["status"] = "failed"
        process.join(timeout=1 if not complete else 5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        parent.close()
    states = [report["load"]["status"], *(r["status"] for r in report["cases"])]
    if complete and ("not_run" in states or process.exitcode != 0):
        complete = False
        report["error_code"] = "incomplete_worker_result"
    report["status"] = "failed" if not complete else aggregate(states)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument(
        "--run-real-model", action="store_true", help="explicitly load offline weights"
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and save a plan only")
    parser.add_argument("--case", choices=("all", *CASE_IDS), default="all")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DEFAULT, help="existing HF_HOME")
    parser.add_argument(
        "--review-in-terminal",
        action="store_true",
        help="explicit private TTY view and manual judgments for synthetic output",
    )
    args = parser.parse_args(argv)
    if args.run_real_model and args.dry_run or args.review_in_terminal and not args.run_real_model:
        parser.error("incompatible modes")
    directory = None
    try:
        suite = load_fixtures()
        settings = settings_for(args.cache_dir.expanduser().resolve())
        selected = list(CASE_IDS) if args.case == "all" else [args.case]
        report = build_report(
            settings, suite, selected, args.run_real_model, args.review_in_terminal
        )
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        directory = Path(
            tempfile.mkdtemp(
                prefix=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + "run-", dir=OUTPUT_ROOT
            )
        )
        write_report(directory, report)
        if not args.run_real_model:
            print(
                f"Plan only: {len(selected)} selected case(s); {settings.model_id} "
                f"at {settings.model_revision}; CPU BF16, 2 threads, serial; "
                "384 output tokens / 300 seconds; offline cache only."
            )
        if args.run_real_model:
            if any(v is None for v in report["environment"]["dependencies"].values()):
                report.update(status="failed", error_code="missing_model_dependencies")
            elif not settings.hf_home.is_dir():
                report.update(status="failed", error_code="missing_offline_cache")
            elif args.review_in_terminal and not sys.stdin.isatty():
                report.update(status="failed", error_code="private_terminal_required")
            else:
                cases = [c for c in suite["cases"] if c["id"] in selected]
                supervise(report, settings, cases, directory, args.review_in_terminal)
        report["finished_at"] = datetime.now().astimezone().isoformat()
        write_report(directory, report)
        print(
            f"Evaluation {report['status']}; report: "
            f".local/model-evaluations/{directory.name}/report.json"
        )
        if report["status"] == "failed":
            print("See docs/testing/model-evaluation.md for offline prerequisites and failures.")
        return {"passed": 0, "not_run": 0, "needs_manual_review": 3, "failed": 1}[report["status"]]
    except Exception:
        # Never stringify arbitrary exceptions, Settings validation inputs or CLI paths.
        if directory is not None:
            report.update(status="failed", error_code="evaluation_failed")
            try:
                write_report(directory, report)
            except OSError:
                pass
        print("Evaluation failed safely. See docs/testing/model-evaluation.md.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
