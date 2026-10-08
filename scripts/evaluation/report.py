import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
from datetime import datetime

from tooling_paths import ROOT

from app.inference.generation import allocate_section_token_limits  # noqa: E402
from evaluation import config
from evaluation.config import (
    CASE_IDS,
    DEPENDENCIES,
    DRAIN_SECONDS,
    LOAD_TIMEOUT_SECONDS,
    TOOL_VERSION,
)
from evaluation.quality import pending_case


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


def build_report(settings, suite, selected, real, manual):
    now = datetime.now().astimezone()
    return {
        "schema_version": 3,
        "tool_version": TOOL_VERSION,
        "started_at": now.isoformat(),
        "timezone": str(now.tzinfo),
        "utc_offset": now.strftime("%z"),
        "finished_at": None,
        "git": git_identity(),
        "tool_sha256": digest_files(
            sorted((ROOT / "scripts/evaluation").glob("*.py"))
            + [ROOT / "scripts/evaluate_model.py", ROOT / "scripts/tooling_paths.py"]
        ),
        "fixture_sha256": hashlib.sha256(config.FIXTURES.read_bytes()).hexdigest(),
        "fixture_version": suite["suite_version"],
        "baseline_kind": "new_synthetic_baseline_not_historical_reproduction",
        "scope": "full_suite" if selected == list(CASE_IDS) else "selected_case",
        "implementation_sha256": digest_files(
            [
                ROOT / f"backend/app/{name}.py"
                for name in (
                    "inference/ollama",
                    "inference/prompts",
                    "inference/generation",
                    "inference/review_output",
                    "config",
                    "model_config",
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
            "inference_backend": "ollama",
            "model_id": settings.ollama_model,
            "model_revision": settings.ollama_model_digest,
            "tokenizer": "ollama",
            "context_tokens": settings.model_context_tokens,
            "keep_alive": settings.model_keep_alive,
            "device": None,
            "quantization": None,
            "concurrency": 1,
            "max_input_tokens": settings.model_max_input_tokens,
            "max_output_tokens": settings.model_max_output_tokens,
            "section_limits": allocate_section_token_limits(settings.model_max_output_tokens),
            "inference_timeout_seconds": settings.inference_timeout_seconds,
            "load_watchdog_seconds": LOAD_TIMEOUT_SECONDS,
            "drain_watchdog_seconds": DRAIN_SECONDS,
            "generation": settings.generation_parameters,
            "enable_thinking": False,
            "seed_policy": "production_stable_per_section",
            "offline": False,
            "transport": "configured_local_ollama",
            "downloads_allowed": False,
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
            "Process RSS measures only the evaluator client, not Ollama model/server memory.",
            "Identical settings do not guarantee cross-platform text or historical latency.",
            "No browser, application API, Kubernetes, AWS or user-data acceptance is performed.",
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
