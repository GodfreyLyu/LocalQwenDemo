"""Offline regression tests; these are never evidence of real-model acceptance."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import deployment.legacy.runtime as demo  # noqa: E402
import deployment.legacy.verify as acceptance  # noqa: E402
import httpx
import pytest


@pytest.mark.unit
@pytest.mark.parametrize("status", ["failed", "running", "queued"])
def test_acceptance_rejects_non_completed_review(status):
    with pytest.raises(demo.DemoError, match="did not complete"):
        acceptance.completed_review({"status": status})


@pytest.mark.unit
def test_acceptance_rejects_wrong_model_and_irrelevant_text():
    value = {
        "status": "completed",
        "model_id": acceptance.MODEL,
        "model_revision": acceptance.REVISION,
        "source_code": acceptance.SOURCE,
        "language": "python",
        "review_result": "## Summary\nAverage values.\n## Findings\nUse good names.\n"
        "## Suggestions\nAdd comments.",
    }
    with pytest.raises(demo.DemoError, match="empty-input"):
        acceptance.completed_review(value)
    value["model_revision"] = "0" * 40
    with pytest.raises(demo.DemoError, match="identity"):
        acceptance.completed_review(value)


@pytest.mark.unit
def test_acceptance_rejects_html_api_fallback():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/html"}, text="<html>SPA</html>"
            )
        ),
        base_url="http://localhost",
    ) as client:
        with pytest.raises(demo.DemoError, match="non-JSON"):
            acceptance.request(client, "GET", "/health/ready", 200)


@pytest.mark.unit
@pytest.mark.recovery
def test_restart_refuses_to_interrupt_active_jobs(monkeypatch):
    def busy():
        raise demo.DemoError("Active reviews")

    monkeypatch.setattr(acceptance, "require_idle", busy)
    mutate = Mock()
    monkeypatch.setattr(acceptance, "k", mutate)
    with pytest.raises(demo.DemoError, match="Active reviews"):
        acceptance.restart_idle(SimpleNamespace(warm_timeout=600))
    mutate.assert_not_called()


@pytest.mark.unit
@pytest.mark.security
def test_log_allowlist_never_emits_bodies_or_raw_lines(monkeypatch, capsys):
    raw = "\n".join(
        [
            "raw password=secret",
            json.dumps(
                {
                    "event": "review_finished",
                    "outcome": "completed",
                    "source_code": "private source",
                    "cookie": "private cookie",
                    "password": "secret",
                }
            ),
        ]
    )
    monkeypatch.setattr(demo, "k", lambda *a: SimpleNamespace(stdout=raw))
    demo.logs()
    output = capsys.readouterr().out
    assert json.loads(output) == {"event": "review_finished", "outcome": "completed"}
    assert "private" not in output and "secret" not in output


@pytest.mark.integration
def test_failed_acceptance_saves_unmeasured_fields_without_claiming_success(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    monkeypatch.setattr(demo, "TARGET", {"cluster_uid": "test-uid"})
    monkeypatch.setattr(acceptance.os, "umask", Mock())
    monkeypatch.setattr(acceptance, "runtime_checks", Mock(side_effect=demo.DemoError("blocked")))
    saved = {}
    monkeypatch.setattr(acceptance, "save", lambda name, value: saved.update({name: value}))
    with pytest.raises(demo.DemoError, match="owner.json.root"):
        acceptance.verify({"port": 8080}, SimpleNamespace())
    report = saved["verification.json"]
    assert report["container_memory"] == report["cold_start"] == "not_measured"
    assert not report["review_completed"]
    assert not report["persistence_verified"]
    assert not report["ui_verified"]


@pytest.mark.unit
@pytest.mark.security
def test_log_allowlist_preserves_safe_startup_diagnostics(monkeypatch, capsys):
    diagnostic = {
        "event": "startup_stage_failed",
        "stage": "cache_validation",
        "error_code": "model_cache_incomplete",
        "exception_type": "ModelCacheIncompleteError",
        "cache_reason": "missing_shard",
        "errno": 2,
    }
    raw = json.dumps({**diagnostic, "exception": "private URL/token", "prompt": "private prompt"})
    monkeypatch.setattr(demo, "k", lambda *a: SimpleNamespace(stdout=raw))
    demo.logs()
    assert json.loads(capsys.readouterr().out) == diagnostic
