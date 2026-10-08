"""Offline regression tests; these are never evidence of real-model acceptance."""

import base64
import contextlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import deployment.legacy.runtime as demo  # noqa: E402
import deployment.legacy.state as state_records  # noqa: E402
import deployment.legacy.target as target  # noqa: E402
import pytest

from scripts.tests.support.paths import ROOT


@pytest.mark.integration
def test_resource_conflict_never_applies(monkeypatch):
    previous = {"kind": "Service", "metadata": {"name": "review-dynamodb"}}
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: previous)
    mutate = Mock()
    monkeypatch.setattr(demo, "k", mutate)
    with pytest.raises(demo.DemoError, match="Ownership conflict"):
        demo.apply([{"kind": "Service", "metadata": {"name": "review-dynamodb"}}], "mine")
    mutate.assert_not_called()


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.parametrize("secret", [None, "", "short"])
def test_existing_invalid_secret_fails_without_rotation(monkeypatch, secret):
    previous = {
        "kind": "Secret",
        "metadata": {"name": "review-secrets", "annotations": {demo.OWNER_KEY: "mine"}},
        "data": {}
        if secret is None
        else {"SIGNING_SECRET": base64.b64encode(secret.encode()).decode()},
    }
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: previous)
    mutate = Mock(return_value=SimpleNamespace(stdout=str(len(secret or ""))))
    monkeypatch.setattr(demo, "k", mutate)
    with pytest.raises(demo.DemoError, match="refusing rotation"):
        demo.ensure_secret("mine")
    assert all(call.args[0] == "get" for call in mutate.call_args_list)


@pytest.mark.integration
@pytest.mark.security
def test_secret_is_reused_and_new_secret_uses_stdin_only(monkeypatch, capsys):
    previous = {
        "kind": "Secret",
        "metadata": {"name": "review-secrets", "annotations": {demo.OWNER_KEY: "mine"}},
        "data": {"SIGNING_SECRET": base64.b64encode(b"x" * 48).decode()},
    }
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: previous)
    mutate = Mock(return_value=SimpleNamespace(stdout="48"))
    monkeypatch.setattr(demo, "k", mutate)
    demo.ensure_secret("mine")
    assert all(call.args[0] == "get" for call in mutate.call_args_list)
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: None)
    demo.ensure_secret("mine")
    assert mutate.call_args.args == ("create", "-f", "-")
    created = json.loads(mutate.call_args.kwargs["data"])
    assert len(created["stringData"]["SIGNING_SECRET"]) >= 48
    assert capsys.readouterr().out == ""


@pytest.mark.integration
@pytest.mark.parametrize(
    "failure_stage",
    [
        "ollama_host_resolution",
        "resource_render",
        "image_build",
        "image_load",
        "dependency_apply",
        "dependency_readiness",
        "dependency_initialization",
        "application_apply",
        "backend_readiness",
        "frontend_readiness",
        None,
    ],
)
def test_deployment_records_actual_failure_stage_and_never_claims_false_success(
    monkeypatch, tmp_path, capsys, failure_stage
):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    (tmp_path / "owner.json").write_text("{}")
    # A failed retry must replace a previous success report, not leave it looking current.
    (tmp_path / "startup.json").write_text(
        json.dumps({"status": "ready", "application_ready": True})
    )
    monkeypatch.setattr(demo, "TARGET", {"cluster_uid": "test-uid"})

    def fail_at(name):
        if failure_stage == name:
            raise demo.DemoError("simulated deployment failure")

    owner = {
        "owner": "mine",
        "root": str(ROOT),
        "profile": "minikube",
        "minikube_home": "/test/.minikube",
        "cluster_uid": "test-uid",
    }
    import deployment.legacy.ollama as minikube_ollama

    resolution_calls = []

    def resolve_host():
        resolution_calls.append(None)
        fail_at("ollama_host_resolution")
        if failure_stage == "resource_render" and len(resolution_calls) == 2:
            return "192.168.71.254"
        return "192.168.70.254"

    monkeypatch.setattr(minikube_ollama, "resolve_host_ip", resolve_host)
    monkeypatch.setattr(demo, "state", lambda **kw: owner)
    monkeypatch.setattr(demo, "guard_cluster", lambda: owner)
    monkeypatch.setattr(demo, "require_local_docker", lambda: None)
    monkeypatch.setattr(demo, "check_images", lambda arch: None)
    monkeypatch.setattr(demo, "ensure_secret", lambda owner: None)
    monkeypatch.setattr(
        state_records, "image_proof", lambda *a: ("sha256:" + "a" * 64, "sha256:" + "b" * 64)
    )

    def rollout(name, seconds):
        phase = {
            "review-dynamodb": "dependency_readiness",
            "review-backend": "backend_readiness",
            "review-frontend": "frontend_readiness",
        }[name]
        fail_at(phase)

    monkeypatch.setattr(demo, "wait_rollout", rollout)
    monkeypatch.setattr(
        demo.subprocess, "call", lambda *a, **kw: 1 if failure_stage == "image_build" else 0
    )
    arch = demo.native_arch(target.platform.machine())
    namespace = {
        "kind": "Namespace",
        "metadata": {"name": demo.NAMESPACE, "uid": "uid", "annotations": {demo.OWNER_KEY: "mine"}},
    }

    def get(kind, name, **kwargs):
        if kind == "namespace":
            return namespace
        if kind == "storageclass":
            return {"provisioner": "k8s.io/minikube-hostpath"}
        return None

    monkeypatch.setattr(demo, "obj", get)
    monkeypatch.setattr(demo, "check_ownership", lambda _: namespace)
    monkeypatch.setattr(demo, "mk", lambda *a, **kw: fail_at("image_load"))

    def execute(args, **kwargs):
        if "daemonset" in args:
            value = json.dumps({"status": {"numberReady": 1}})
        elif args[:3] == ["docker", "container", "inspect"]:
            value = "node-id"
        else:
            value = arch
        return SimpleNamespace(stdout=value)

    monkeypatch.setattr(demo, "run", execute)
    monkeypatch.setattr(
        demo,
        "k",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps({"items": [{"status": {"nodeInfo": {"architecture": arch}}}]})
        ),
    )
    # Rendering is separately exercised against real kubectl in the tests above.
    rendered = [
        {"kind": "Deployment", "metadata": {"name": name}}
        for name in ("review-dynamodb", "review-backend", "review-frontend")
    ]
    render_calls = []

    def render_step(*args):
        render_calls.append(args)
        return rendered

    monkeypatch.setattr(demo, "render", render_step)
    applied = []

    def apply_step(resources, owner):
        phase = (
            "application_apply"
            if any(r["metadata"]["name"] == "review-backend" for r in resources)
            else "dependency_apply"
        )
        fail_at(phase)
        applied.extend(resources)

    monkeypatch.setattr(demo, "apply", apply_step)
    monkeypatch.setattr(demo, "init_users", lambda: fail_at("dependency_initialization"))
    plan = {
        "architecture": arch,
        "storage_class": "standard",
        "reusable_images": {},
        "fingerprints": {},
        "cni": {"policy_support": "not_enforced"},
        "report": {"status": "failed", "blockers": ["Insufficient target cluster capacity"]},
    }
    monkeypatch.setattr(
        demo, "main", lambda: demo.up(SimpleNamespace(port=8080, cold_timeout=3600), plan)
    )
    assert demo.cli() == (1 if failure_stage else 0)
    report = json.loads((tmp_path / "startup.json").read_text())
    assert report["status"] == ("failed" if failure_stage else "ready")
    assert report["stage"] == (failure_stage or "complete")
    assert report["application_ready"] == (failure_stage is None)
    assert report["preflight"]["status"] == "failed"
    assert report["diagnostic_warnings"] == plan["report"]["blockers"]
    assert not report["review_completed"] and not report["ui_verified"]
    if not failure_stage:
        assert render_calls[0][-1] == "192.168.70.254"
        expected_network = {
            "hostname": "host.minikube.internal",
            "ipv4": "192.168.70.254",
            "port": 11434,
        }
        for name in ("plan.json", "deployment.json"):
            assert json.loads((tmp_path / name).read_text())["ollama_network"] == expected_network
    output = capsys.readouterr()
    if failure_stage:
        assert f"Deployment failed during {failure_stage}" in output.err
        assert "Ready is not inference acceptance" not in output.out
    if failure_stage in {
        "ollama_host_resolution",
        "resource_render",
        "image_build",
        "image_load",
        "dependency_apply",
    }:
        assert not applied
    elif failure_stage in {
        "dependency_readiness",
        "dependency_initialization",
        "application_apply",
    }:
        assert [r["metadata"]["name"] for r in applied] == ["review-dynamodb"]


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.parametrize("final_only", [False, True])
def test_report_write_failure_is_nonzero_and_cannot_claim_success(
    diagnostic_environment, monkeypatch, capsys, final_only
):
    plan = demo.deployment_plan(SimpleNamespace(port=8080, storage_class=None))
    demo.STATE.mkdir()
    original = '{"status": "ready", "attempt_id": "old"}'
    (demo.STATE / "startup.json").write_text(original)
    real_save = demo.save

    def cannot_save(name, value):
        if not final_only or value["status"] == "ready":
            raise OSError("private filesystem details")
        real_save(name, value)

    deployment = Mock(return_value={})
    monkeypatch.setattr(demo, "save", cannot_save)
    monkeypatch.setattr(demo, "deploy_application", deployment)
    monkeypatch.setattr(demo, "main", lambda: demo.up(SimpleNamespace(), plan))
    assert demo.cli() == 1
    output = capsys.readouterr()
    assert "private filesystem details" not in output.err
    if final_only:
        report = json.loads((demo.STATE / "startup.json").read_text())
        assert report["status"] == "failed" and report["stage"] == "report_save"
        assert not report["application_ready"]
    else:
        deployment.assert_not_called()
        assert "existing report may be stale" in output.err
        assert (demo.STATE / "startup.json").read_text() == original


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.recovery
@pytest.mark.parametrize("interrupted", [False, True])
def test_unexpected_deployment_error_or_interrupt_is_recorded_and_never_continued(
    diagnostic_environment, monkeypatch, capsys, interrupted
):
    plan = demo.deployment_plan(SimpleNamespace(port=8080, storage_class=None))
    demo.STATE.mkdir()

    def failed(args, plan, stage):
        stage("dependency_initialization")
        raise KeyboardInterrupt() if interrupted else RuntimeError("private response body")

    monkeypatch.setattr(demo, "deploy_application", failed)
    monkeypatch.setattr(demo, "main", lambda: demo.up(SimpleNamespace(), plan))
    assert demo.cli() == (130 if interrupted else 1)
    report_text = (demo.STATE / "startup.json").read_text()
    report = json.loads(report_text)
    assert report["status"] == ("interrupted" if interrupted else "failed")
    assert report["stage"] == "dependency_initialization"
    assert not report["application_ready"]
    assert "private response body" not in report_text + capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.requires_kubectl
def test_up_does_not_swallow_unexpected_diagnostic_exceptions(
    diagnostic_environment, monkeypatch, capsys
):
    monkeypatch.setattr(target, "memory_usage", Mock(side_effect=RuntimeError("private details")))
    deploy = Mock()
    monkeypatch.setattr(demo, "up", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    deploy.assert_not_called()
    assert "private details" not in capsys.readouterr().err
    assert not demo.STATE.exists()


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.security
@pytest.mark.parametrize(
    "conflict", ["state", "namespace", "Deployment", "PersistentVolumeClaim", "Secret"]
)
def test_up_ownership_conflicts_stop_before_diagnostics_or_any_state_overwrite(
    monkeypatch, tmp_path, resources, conflict
):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    monkeypatch.setattr(
        demo,
        "TARGET",
        {
            "root": "checkout",
            "profile": "existing",
            "cluster_uid": "cluster",
            "minikube_home": "/existing/.minikube",
        },
    )
    monkeypatch.setattr(demo.os, "environ", dict(demo.os.environ))
    owner = demo.TARGET | {"owner": "ours", "namespace_uid": "namespace"}
    if conflict == "state":
        owner["cluster_uid"] = "foreign-cluster"
    original = json.dumps(owner)
    (tmp_path / "owner.json").write_text(original)
    (tmp_path / "kubeconfig").write_text("original private config")

    @contextlib.contextmanager
    def connected(_):
        yield {}

    def existing(kind, name, **kwargs):
        if kind.lower() == "namespace" or kind == conflict:
            return {
                "kind": kind,
                "metadata": {
                    "name": name,
                    "uid": "namespace",
                    "annotations": {demo.OWNER_KEY: "foreign" if kind == conflict else "ours"},
                },
            }
        return None

    monkeypatch.setattr(target, "connected_target", connected)
    monkeypatch.setattr(demo, "obj", existing)
    monkeypatch.setattr(demo, "render", lambda *a: resources)
    checks = Mock()
    deploy = Mock()
    monkeypatch.setattr(target, "preflight", checks)
    monkeypatch.setattr(demo, "up", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    checks.assert_not_called()
    deploy.assert_not_called()
    assert (tmp_path / "owner.json").read_text() == original
    assert (tmp_path / "kubeconfig").read_text() == "original private config"
    assert not (tmp_path / "startup.json").exists()


@pytest.mark.integration
@pytest.mark.requires_kubectl
def test_up_still_rejects_existing_pvc_class_mismatch(diagnostic_environment, monkeypatch):
    diagnostic_environment["owned"] = True
    monkeypatch.setattr(
        demo,
        "obj",
        lambda kind, *a, **kw: (
            {"provisioner": "k8s.io/minikube-hostpath"}
            if kind == "storageclass"
            else {"spec": {"storageClassName": "foreign"}}
        ),
    )
    deploy = Mock()
    monkeypatch.setattr(demo, "up", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    deploy.assert_not_called()
    assert not demo.STATE.exists()


@pytest.mark.integration
@pytest.mark.security
def test_existing_unowned_namespace_is_never_adopted(monkeypatch):
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: {"kind": "Namespace", "metadata": {}})
    with pytest.raises(demo.DemoError, match="Ownership conflict"):
        demo.check_ownership(None)


@pytest.mark.integration
def test_same_inputs_reuse_only_matching_native_image_id(monkeypatch):
    owner = {
        "images": {"review-backend": "review-backend:old"},
        "build_fingerprints": {"review-backend": "fingerprint"},
        "image_ids": {"review-backend": "sha256:expected"},
    }
    calls = Mock(return_value=SimpleNamespace(returncode=0, stdout="arm64 sha256:expected"))
    monkeypatch.setattr(demo, "run", calls)
    assert (
        target.reusable_images(owner, {"review-backend": "fingerprint"}, "arm64") == owner["images"]
    )
    assert target.reusable_images(owner, {"review-backend": "changed"}, "arm64") == {}
    calls.return_value.stdout = "arm64 sha256:replaced"
    assert target.reusable_images(owner, {"review-backend": "fingerprint"}, "arm64") == {}
