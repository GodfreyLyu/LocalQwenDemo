"""Deterministic evaluator contracts; no real model, cache download, cluster or AWS calls."""

import io
import json
import logging
import multiprocessing
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate_model as ev  # noqa: E402

SECRET = "PRIVATE_SENTINEL_cookie_csrf_prompt_body"
VALID = (
    "## Summary\nThe average function computes a mean.\n\n"
    "## Findings\nEmpty values cause division by zero.\n\n"
    "## Suggestions\nReject an empty input before division."
)


def metric_record(**updates):
    """Model log contract only; extra secret fields must never survive capture."""
    fields = {
        "duration_ms": 1200,
        "generated_tokens": 30,
        "output_token_limit": 384,
        "output_limit_reached": False,
        "worker_intraop_threads": 2,
        "worker_interop_threads": 8,
        "section_generated_tokens": dict.fromkeys(ev.REQUIRED_SECTIONS, 10),
        "section_input_tokens": dict.fromkeys(ev.REQUIRED_SECTIONS, 60),
        "section_prepare_ms": dict.fromkeys(ev.REQUIRED_SECTIONS, 2),
        "section_generation_ms": dict.fromkeys(ev.REQUIRED_SECTIONS, 400),
        "section_first_token_ms": dict.fromkeys(ev.REQUIRED_SECTIONS, 10),
        "section_limits_reached": dict.fromkeys(ev.REQUIRED_SECTIONS, False),
        "section_trailing_fragments_removed": dict.fromkeys(ev.REQUIRED_SECTIONS, False),
        "prompt": SECRET,
        "token_ids": [SECRET],
        "body": SECRET,
    }
    fields.update(updates)
    return logging.makeLogRecord(
        {"msg": "model_generation_finished", "levelno": logging.INFO, **fields}
    )


@pytest.fixture
def case():
    return ev.load_fixtures()["cases"][1]


@pytest.fixture
def settings(tmp_path):
    return ev.settings_for(tmp_path / "cache")


def run_case(settings, case, result=VALID, error=None, manual=None, metrics=True, clock=None):
    capture = ev.MetricCapture()

    def review(*args):
        if metrics:
            capture.emit(metric_record())
        if error:
            raise error
        return result

    kwargs = {"clock": clock} if clock else {}
    return ev.evaluate_case(
        SimpleNamespace(review=review), settings, case, capture, manual, **kwargs
    )


@pytest.mark.parametrize("args", [[], ["--dry-run"], ["--dry-run", "--case", "average"]])
def test_plan_only_has_no_model_cache_or_network_side_effects(monkeypatch, tmp_path, args):
    out = tmp_path / "reports"
    monkeypatch.setattr(ev, "OUTPUT_ROOT", out)
    blocked = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(ev, "TransformersModel", blocked)
    monkeypatch.setattr(ev, "supervise", blocked)
    monkeypatch.setattr(ev, "offline_runtime", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    cache = tmp_path / "absent"
    assert ev.main([*args, "--cache-dir", str(cache)]) == 0
    report = json.loads(next(out.glob("*/report.json")).read_text())
    assert report["status"] == "not_run" and report["load"]["status"] == "not_run"
    assert all(r["status"] == "not_run" for r in report["cases"])
    assert report["parameters"]["section_limits"] == ev.allocate_section_token_limits(384)
    assert not cache.exists()
    blocked.assert_not_called()


def test_env_does_not_change_fixed_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_DTYPE", "float32")
    monkeypatch.setenv("MODEL_REVISION", "0" * 40)
    monkeypatch.setenv("MODEL_CPU_THREADS", "15")
    monkeypatch.setenv("INFERENCE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("HF_TOKEN", SECRET)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MODEL_MAX_OUTPUT_TOKENS=64\nSIGNING_SECRET=bad\n")
    settings = ev.settings_for(tmp_path / "cache")
    assert (settings.model_dtype, settings.model_revision, settings.model_cpu_threads) == (
        "bfloat16",
        ev.MODEL_REVISION,
        2,
    )
    assert settings.model_max_output_tokens == 384 and settings.inference_timeout_seconds == 300


def test_fixtures_are_complete_synthetic_and_invalid_schema_fails(monkeypatch, tmp_path):
    suite = ev.load_fixtures()
    assert tuple(c["id"] for c in suite["cases"]) == ev.CASE_IDS
    assert "not reconstructed" in suite["provenance"]
    for c in suite["cases"]:
        compile(c["source"], "synthetic-fixture", "exec")  # Parse only; never execute submissions.
    p = tmp_path / "invalid.json"
    suite["cases"].pop()
    p.write_text(json.dumps(suite))
    monkeypatch.setattr(ev, "FIXTURES", p)
    with pytest.raises(ev.EvaluationError):
        ev.load_fixtures()


@pytest.mark.parametrize(
    "args",
    [
        ["--case", SECRET],
        ["--unknown", SECRET],
        ["--run-real-model", "--dry-run"],
        ["--review-in-terminal"],
    ],
)
def test_cli_invalid_inputs_are_sanitized(args, capsys):
    with pytest.raises(SystemExit) as exc:
        ev.main(args)
    assert exc.value.code == 2
    assert SECRET not in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["cache", "dependencies"])
def test_preconditions_fail_without_worker(monkeypatch, tmp_path, missing):
    monkeypatch.setattr(ev, "OUTPUT_ROOT", tmp_path / "reports")
    dependency_versions = dict.fromkeys(ev.DEPENDENCIES, "test")
    if missing == "dependencies":
        dependency_versions["torch"] = None
    monkeypatch.setattr(ev, "versions", lambda: dependency_versions)
    spy = Mock(side_effect=AssertionError("must not start"))
    monkeypatch.setattr(ev, "supervise", spy)
    assert ev.main(["--run-real-model", "--cache-dir", str(tmp_path / "missing")]) == 1
    report = json.loads(next((tmp_path / "reports").glob("*/report.json")).read_text())
    assert report["status"] == "failed"
    assert all(c["status"] == "not_run" for c in report["cases"])
    spy.assert_not_called()


def test_keyword_hints_never_complete_semantic_acceptance(settings, case):
    row = run_case(settings, case)
    assert row["status"] == "needs_manual_review"
    assert row["hints"]["concept_groups_present"] == [True, True]
    assert all(row["checks"][k] == "needs_manual_review" for k in ev.MANUAL_CHECKS)
    assert row["checks"]["production_contract"] == "passed"
    assert row["checks"]["metrics_contract"] == "passed"
    assert row["metrics"]["generated_tokens"] == 30
    assert row["metrics"]["peak_process_rss_mib"] > 0
    assert VALID not in json.dumps(row) and case["source"] not in json.dumps(row)


@pytest.mark.parametrize(
    "error,code",
    [
        (
            ev.AppError(
                "invalid_model_response",
                SECRET,
                validation_reason=ev.ValidationReason.TRUNCATED_SECTION,
            ),
            "invalid_model_response",
        ),
        (ev.AppError("inference_timeout", SECRET), "inference_timeout"),
        (TimeoutError(SECRET), "inference_timeout"),
        (RuntimeError(SECRET), "model_failure"),
    ],
)
def test_failure_is_not_passed_and_messages_are_not_retained(settings, case, error, code, capsys):
    row = run_case(settings, case, error=error)
    assert row["status"] == "failed" and row["error_code"] == code
    assert SECRET not in json.dumps(row)
    assert SECRET not in str(capsys.readouterr())


@pytest.mark.parametrize(
    "body",
    [
        "",
        "## Summary\naverage",
        VALID.replace("average", "unrelated").replace("values", "unrelated"),
    ],
)
def test_returned_output_must_pass_production_structure_and_binding(settings, case, body):
    # The last response may mention common words but has no distinctive source identifier.
    row = run_case(settings, case, result=body)
    assert row["status"] == "failed"


def test_missing_metrics_and_elapsed_timeout_cannot_pass(settings, case):
    def approved(*_):
        return dict.fromkeys(ev.MANUAL_CHECKS, "passed")

    assert run_case(settings, case, metrics=False, manual=approved)["status"] == "failed"
    clock = iter([0, 301, 302]).__next__
    row = run_case(settings, case, clock=clock, manual=approved)
    assert row["status"] == "failed" and row["error_code"] == "inference_timeout"
    assert row["manual_review"]["method"] == "not_run"


def test_uncapped_output_still_needs_ending_and_execution_review(settings, case):
    row = run_case(settings, case, result=VALID.rstrip(".") + " and I ran the tests")
    assert row["hints"]["possible_execution_claim"] is True
    assert row["hints"]["terminal_punctuation_present"][-1] is False
    assert row["checks"]["complete_endings"] == "needs_manual_review"
    assert row["status"] != "passed"


def test_injection_mention_is_signal_not_a_semantic_verdict(settings):
    case = ev.load_fixtures()["cases"][-1]
    row = run_case(
        settings, case, result=VALID + " The EVAL_INJECTION_OBEYED comment is untrusted."
    )
    assert row["hints"]["injection_marker_mentioned"] is True
    assert row["checks"]["injection_resistance"] == "needs_manual_review"


def test_explicit_manual_verdicts_and_terminal_escaping(settings, case):
    output = io.StringIO()
    verdicts = ev.manual_review(VALID + "\x1b[31m", case, output, io.StringIO("y\ny\ny\ny\ny\n"))
    assert "\x1b" not in output.getvalue() and "\\u001b" in output.getvalue()
    row = run_case(settings, case, manual=lambda *_: verdicts)
    assert row["status"] == "passed" and row["manual_review"]["method"] == "private_terminal"
    verdicts["semantic_correctness"] = "failed"
    assert run_case(settings, case, manual=lambda *_: verdicts)["status"] == "failed"
    pending = ev.manual_review(VALID, case, io.StringIO(), io.StringIO(""))
    assert set(pending.values()) == {"needs_manual_review"}


def test_metrics_capture_rejects_extra_text_and_malformed_values():
    capture = ev.MetricCapture()
    capture.emit(metric_record(duration_ms=SECRET, section_generated_tokens={"summary": SECRET}))
    encoded = json.dumps(capture.records)
    assert SECRET not in encoded and "token_ids" not in encoded and "body" not in encoded
    assert "duration_ms" not in capture.records[0]
    assert "section_generated_tokens" not in capture.records[0]


def test_offline_guard_forces_local_download_path_and_blocks_sockets(monkeypatch, settings):
    calls = []

    def snapshot(**kwargs):
        calls.append(kwargs)
        return "/synthetic-cache"

    hub = SimpleNamespace(
        snapshot_download=snapshot, constants=SimpleNamespace(HF_HUB_OFFLINE=False)
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(ev, "validate_model_snapshot", lambda _: None)
    with ev.offline_runtime(settings):
        hub.snapshot_download(repo_id=settings.model_id, local_files_only=False, token=SECRET)
        with pytest.raises(ev.EvaluationError, match="network_disabled"):
            socket.create_connection(("127.0.0.1", 9))
        with socket.socket() as s, pytest.raises(ev.EvaluationError, match="network_disabled"):
            s.connect(("127.0.0.1", 9))
    assert all(c["local_files_only"] is True and c["token"] is False for c in calls)
    assert hub.constants.HF_HUB_OFFLINE is False


def test_incomplete_cache_stops_before_model_load(monkeypatch, settings):
    hub = SimpleNamespace(
        snapshot_download=lambda **_: "/synthetic-cache",
        constants=SimpleNamespace(HF_HUB_OFFLINE=False),
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    def fail(_):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ev, "validate_model_snapshot", fail)
    entered = False
    with pytest.raises(RuntimeError):
        with ev.offline_runtime(settings):
            entered = True
    assert not entered


def test_fresh_private_reports_and_plan_metadata(monkeypatch, tmp_path):
    out = tmp_path / "out"
    monkeypatch.setattr(ev, "OUTPUT_ROOT", out)
    assert ev.main(["--dry-run"]) == ev.main(["--dry-run"]) == 0
    paths = list(out.glob("*/report.json"))
    assert len(paths) == 2
    for path in paths:
        report = json.loads(path.read_text())
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert report["git"]["revision"] and type(report["git"]["dirty"]) is bool
        assert len(report["fixture_sha256"]) == len(report["implementation_sha256"]) == 64
        assert report["parameters"]["offline"] and not report["parameters"]["downloads_allowed"]
        assert report["started_at"] and report["timezone"] and report["utc_offset"]
        assert "signing_secret" not in path.read_text().lower()


def test_cli_dry_run_from_other_directory_imports_no_model_dependencies(tmp_path):
    code = (
        f"import sys; sys.path.insert(0, {str(ev.ROOT / 'scripts')!r}); "
        f"import evaluate_model as e; from pathlib import Path; "
        f"e.OUTPUT_ROOT=Path({str(tmp_path)!r}); "
        "assert e.main(['--dry-run', '--case', 'square'])==0; "
        "assert 'torch' not in sys.modules and 'transformers' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@contextmanager
def fake_offline(settings):
    yield


class FakeRuntimeModel:
    """Exercise the real worker/report boundary with synthetic content and deliberately noisy IO."""

    def __init__(self, settings):
        self.model = SimpleNamespace(device="cpu", training=False, dtype="torch.bfloat16")

    def load(self):
        os.write(1, SECRET.encode())
        os.write(2, SECRET.encode())

    def review(self, *_):
        ev.logger.handle(metric_record())
        return VALID


def test_real_worker_lifecycle_with_fake_model(monkeypatch, tmp_path, settings, case, capfd):
    # Fork preserves these in-memory doubles. No model libraries are imported or loaded.
    monkeypatch.setattr(ev, "offline_runtime", fake_offline)
    monkeypatch.setattr(ev, "TransformersModel", FakeRuntimeModel)
    report = ev.build_report(settings, ev.load_fixtures(), [case["id"]], True, False)
    ev.supervise(report, settings, [case], tmp_path, False, multiprocessing.get_context("fork"))
    assert report["status"] == "needs_manual_review"
    assert report["load"]["status"] == "passed"
    assert SECRET not in str(capfd.readouterr()) and SECRET not in json.dumps(report)


def hanging_worker(connection, *_):
    connection.send(("load", {"status": "passed", "seconds": 0.1, "peak_process_rss_mib": 10}))
    connection.send(("stage", 0, "inference"))
    import time

    time.sleep(60)


def test_watchdog_reaps_only_its_worker_and_reports_incomplete_case(
    monkeypatch, tmp_path, settings, case
):
    monkeypatch.setattr(ev, "worker", hanging_worker)
    monkeypatch.setattr(ev, "DRAIN_SECONDS", 0)
    settings.inference_timeout_seconds = 0.05
    before = {p.pid for p in multiprocessing.active_children()}
    report = ev.build_report(settings, ev.load_fixtures(), [case["id"]], True, False)
    ev.supervise(report, settings, [case], tmp_path, False, multiprocessing.get_context("fork"))
    assert report["status"] == "failed" and report["error_code"] == "worker_timeout"
    assert report["cases"][0]["status"] == "failed"
    assert {p.pid for p in multiprocessing.active_children()} == before


@contextmanager
def invalid_offline(settings):
    raise RuntimeError(SECRET)
    yield  # pragma: no cover - context-manager shape only


class LoadFailureModel(FakeRuntimeModel):
    def load(self):
        raise RuntimeError(SECRET)


@pytest.mark.parametrize("stage", ["cache", "load"])
def test_worker_preflight_and_load_failures_remain_sanitized(
    monkeypatch, tmp_path, settings, case, stage, capfd
):
    monkeypatch.setattr(
        ev, "offline_runtime", invalid_offline if stage == "cache" else fake_offline
    )
    monkeypatch.setattr(ev, "TransformersModel", LoadFailureModel)
    report = ev.build_report(settings, ev.load_fixtures(), [case["id"]], True, False)
    ev.supervise(report, settings, [case], tmp_path, False, multiprocessing.get_context("fork"))
    assert report["status"] == "failed" and report["load"]["status"] == "failed"
    assert report["error_code"] == (
        "offline_cache_invalid" if stage == "cache" else "model_load_failed"
    )
    assert report["cases"][0]["status"] == "not_run"
    assert report["load"]["seconds"] >= 0
    assert SECRET not in json.dumps(report) and SECRET not in str(capfd.readouterr())


def empty_worker(connection, *_):
    connection.send(("done",))
    connection.close()


def test_spawn_protocol_cannot_accept_done_without_results(monkeypatch, tmp_path, settings, case):
    # The production spawn context is exercised with a tiny picklable protocol double.
    monkeypatch.setattr(ev, "worker", empty_worker)
    report = ev.build_report(settings, ev.load_fixtures(), [case["id"]], True, False)
    ev.supervise(report, settings, [case], tmp_path, False)
    assert report["status"] == "failed" and report["error_code"] == "incomplete_worker_result"


def test_metric_field_units_and_nulls_are_preserved(settings):
    capture = ev.MetricCapture()
    capture.emit(metric_record(section_first_token_ms=dict.fromkeys(ev.REQUIRED_SECTIONS, None)))
    metrics = capture.records[0]
    assert metrics["duration_ms"] == 1200 and metrics["section_generation_ms"]["summary"] == 400
    assert metrics["generated_tokens"] == 30
    assert metrics["section_first_token_ms"]["summary"] is None
    assert not ev.metrics_valid(metrics, settings)


@pytest.mark.parametrize(
    "status,exit_code", [("needs_manual_review", 3), ("failed", 1), ("passed", 0)]
)
def test_cli_exit_codes_follow_run_outcome(monkeypatch, tmp_path, status, exit_code):
    monkeypatch.setattr(ev, "OUTPUT_ROOT", tmp_path / "reports")
    monkeypatch.setattr(ev, "versions", lambda: dict.fromkeys(ev.DEPENDENCIES, "test"))

    def fake_supervisor(report, *_):
        report["status"] = status

    monkeypatch.setattr(ev, "supervise", fake_supervisor)
    assert ev.main(["--run-real-model", "--cache-dir", str(tmp_path)]) == exit_code


@pytest.mark.parametrize("system,raw", [("darwin", 1024 * 1024), ("linux", 1024)])
def test_peak_rss_platform_units(monkeypatch, system, raw):
    monkeypatch.setattr(ev.sys, "platform", system)
    monkeypatch.setattr(ev.resource, "getrusage", lambda _: SimpleNamespace(ru_maxrss=raw))
    assert ev.peak_rss_mib() == 1.0
