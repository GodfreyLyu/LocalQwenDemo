"""Offline regression tests; these are never evidence of real-model acceptance."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import deployment.legacy.runtime as demo  # noqa: E402
import deployment.legacy.target as target  # noqa: E402
import deployment.legacy.verify as acceptance  # noqa: E402
import pytest

from scripts.tests.support.deployment import roomy_budget


@pytest.mark.unit
def test_resource_budget_rejects_realistic_low_disk_and_memory():
    args = roomy_budget()
    assert not target.capacity_errors(**args)
    args.update(
        host_available=2 * demo.GIB,
        docker_total=8 * demo.GIB,
        node_cap=4 * demo.GIB,
        disk_free=12 * demo.GIB,
    )
    errors = target.capacity_errors(**args)
    assert any("Host memory pressure" in e for e in errors)
    assert any("Insufficient Docker quota" in e for e in errors)
    assert any("target node" in e for e in errors)
    assert any("Insufficient host disk space" in e for e in errors)


@pytest.mark.unit
def test_repeat_budget_counts_only_incremental_memory():
    args = roomy_budget()
    args.update(
        own_used=7 * demo.GIB,
        host_available=2 * demo.GIB,
        node_used=8 * demo.GIB,
        docker_used=8 * demo.GIB,
    )
    assert not target.capacity_errors(**args)
    args["own_used"] = None
    assert any("not measured" in e for e in target.capacity_errors(**args))


@pytest.mark.unit
@pytest.mark.parametrize("unavailable", [None, "", "max"])
def test_memory_report_distinguishes_missing_measurements_from_zero(monkeypatch, unavailable):
    monkeypatch.setattr(
        acceptance,
        "backend_python",
        lambda _: SimpleNamespace(
            stdout=json.dumps({"memory.current": "0", "memory.peak": unavailable})
        ),
    )
    assert acceptance.memory_sample() == {"memory.current": 0, "memory.peak": "not_measured"}


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.parametrize("owned, metrics_available", [(False, False), (True, False), (True, True)])
def test_preflight_report_uses_stable_english_measurement_statuses(
    diagnostic_environment, owned, metrics_available
):
    diagnostic_environment.update(owned=owned, metrics=metrics_available)
    plan = demo.deployment_plan(SimpleNamespace(port=8080, storage_class=None))
    report = plan["report"]
    assert report["pod_metrics"] == ("available" if metrics_available else "not_measured")
    assert report["own_usage_bytes"] == ("not_measured" if owned and not metrics_available else 0)
    assert report["node_observed_usage_gib"] == 1
    assert bool(report["blockers"]) == (not metrics_available)
    assert report["status"] == ("passed" if metrics_available else "failed")


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.parametrize("command", ["doctor", "up"])
@pytest.mark.parametrize(
    "issue",
    [
        "host_memory",
        "docker_quota",
        "node_capacity",
        "host_disk",
        "vm_disk",
        "missing_metrics",
        "missing_host_memory",
        "missing_docker_stats",
        "malformed_docker_stats",
        "missing_node_limits",
        "missing_workload_inventory",
        "missing_node_allocatable",
        "missing_version",
        "version_skew",
        "not_ready",
        "taint",
        "missing_vm_disk",
        "missing_own_memory",
    ],
)
def test_diagnostics_fail_doctor_but_warn_and_continue_up(
    diagnostic_environment, monkeypatch, capsys, command, issue
):
    diagnostic_environment.update(
        issue=issue,
        metrics=issue not in {"missing_metrics", "missing_own_memory"},
        owned=issue == "missing_own_memory",
    )
    entered = []

    def deploy(args, plan, stage):
        stage("image_build", "review-backend")
        entered.append(plan)
        return {}

    monkeypatch.setattr(demo, "deploy_application", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", command])
    assert demo.cli() == (1 if command == "doctor" else 0)
    output = capsys.readouterr()
    if command == "doctor":
        assert "Diagnostics failed" in output.err
        assert not entered and not demo.STATE.exists()
    else:
        assert entered and "WARNING:" in output.out
        assert "Deployment will still be attempted" in output.out
        report = json.loads((demo.STATE / "startup.json").read_text())
        assert report["status"] == "ready" and report["application_ready"]
        assert report["preflight"]["status"] == "failed"
        assert report["diagnostic_warnings"] == report["preflight"]["blockers"]
        assert not report["review_completed"] and not report["ui_verified"]
        if issue.startswith("missing_") or issue == "malformed_docker_stats":
            assert any(
                m["status"] == "not_measured" for m in report["preflight"]["measurements"].values()
            )


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.parametrize(
    "change, message",
    [
        ({"node_arch": "amd64"}, "architectures differ"),
        ({"docker_arch": "amd64"}, "Docker VM architecture"),
        ({"provisioner": "foreign"}, "StorageClass"),
    ],
)
def test_up_still_blocks_incompatible_architecture_and_storage(
    diagnostic_environment, monkeypatch, change, message, capsys
):
    diagnostic_environment.update(change, issue="host_memory")
    deployment = Mock()
    monkeypatch.setattr(demo, "up", deployment)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    assert message in capsys.readouterr().err
    deployment.assert_not_called()
    assert not demo.STATE.exists()


@pytest.mark.integration
@pytest.mark.requires_kubectl
def test_doctor_succeeds_only_with_complete_sufficient_diagnostics(
    diagnostic_environment, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "doctor"])
    assert demo.cli() == 0
    assert '"status": "passed"' in capsys.readouterr().out
    assert not demo.STATE.exists()


@pytest.mark.unit
@pytest.mark.security
def test_resource_projection_handles_unbounded_pods_without_fetching_secrets(monkeypatch):
    row = (
        "other\tapp\tnode\tRunning\tuid\t"
        '{"requests":{"memory":"256Mi","cpu":"100m"}}\t\t\tfirst second\t\n'
    )
    calls = Mock(return_value=SimpleNamespace(stdout=row))
    monkeypatch.setattr(demo, "k", calls)
    pods = target.workload_inventory()
    budget = target.sum_budgets([pods[0]["spec"]])
    assert budget["memory_request"] == 256 * 1024**2
    assert budget["unbounded_memory"] == 2
    projection = calls.call_args.args[-1]
    assert "containers[*].resources" in projection
    assert ".env" not in projection and ".command" not in projection


@pytest.mark.unit
def test_owned_resource_credit_requires_deployment_replicaset_chain(monkeypatch):
    monkeypatch.setattr(
        demo,
        "obj",
        lambda kind, name, **kw: {
            "metadata": {"uid": "owned-deployment", "annotations": {demo.OWNER_KEY: "mine"}}
        },
    )
    monkeypatch.setattr(
        demo,
        "k",
        lambda *a: SimpleNamespace(
            stdout="owned-rs\towned-deployment\nother-rs\tother-deployment\n"
        ),
    )
    pods = [
        {"name": "ours", "namespace": demo.NAMESPACE, "owners": ["owned-rs"]},
        {"name": "foreign", "namespace": demo.NAMESPACE, "owners": ["other-rs"]},
        {"name": "outsider", "namespace": "other", "owners": ["owned-rs"]},
    ]
    assert target.owned_pod_names({"owner": "mine"}, pods) == {"ours"}


@pytest.mark.unit
def test_cni_without_policy_support_is_reported_not_installed(monkeypatch):
    monkeypatch.setattr(demo, "TARGET", {"node_name": "minikube"})
    calls = Mock(
        return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps({"plugins": [{"type": "bridge"}, {"type": "portmap"}]})
        )
    )
    monkeypatch.setattr(demo, "run", calls)
    assert target.cni_status()["policy_support"] == "not_enforced"
    assert calls.call_args.args[0][:3] == ["docker", "exec", "minikube"]


@pytest.mark.unit
def test_docker_node_cpu_cap_cannot_be_hidden_by_high_allocatable():
    args = roomy_budget()
    args["node_cpu_cap"] = 2
    assert any("Insufficient target cluster capacity" in e for e in target.capacity_errors(**args))
    assert (
        target.docker_cpu_limit({"nano": 2_000_000_000, "quota": 0, "period": 0, "cpuset": ""}, 10)
        == 2
    )
    assert (
        target.docker_cpu_limit(
            {"nano": 0, "quota": 150000, "period": 100000, "cpuset": "0-3,6"}, 10
        )
        == 1.5
    )
