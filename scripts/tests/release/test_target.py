"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import json
from types import SimpleNamespace

import deployment.common.target as helm_target  # noqa: E402
import deployment.legacy.helm_deploy as helm_deploy  # noqa: E402
import pytest
import release.snapshot_impl as snapshot  # noqa: E402

from scripts.tests.support.paths import ROOT


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "8.8.8.8", "::1"])
def test_ollama_discovery_rejects_nonprivate_target(ip):
    with pytest.raises(ValueError):
        helm_target.private_host(ip)


@pytest.mark.integration
@pytest.mark.security
def test_context_is_always_explicit_and_legacy_ownership_is_not_adopted(tmp_path):
    target = helm_target.Target("second-cluster", "review", tmp_path / "kubeconfig", "node", {})
    assert target.kube[1:5] == [
        "--kubeconfig",
        str(tmp_path / "kubeconfig"),
        "--context",
        "second-cluster",
    ]
    assert target.helm[1:5] == [
        "--kubeconfig",
        str(tmp_path / "kubeconfig"),
        "--kube-context",
        "second-cluster",
    ]
    with pytest.raises(ValueError, match="automatic takeover"):
        helm_deploy.check_ownership(
            {"kind": "Deployment", "metadata": {"name": "review-backend"}},
            "local-review",
            "review",
        )


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.parametrize("host_ollama", [False, True])
def test_legacy_wrapper_checks_policy_ownership_for_both_endpoints(
    monkeypatch, tmp_path, host_ollama
):
    def kubectl(*args):
        if args[1] == "nodes":
            items = [
                {
                    "status": {
                        "allocatable": {"cpu": "8", "memory": "16Gi"},
                        "conditions": [{"type": "Ready", "status": "True"}],
                    }
                }
            ]
        else:
            assert args[1] == "pods"
            items = []
        return SimpleNamespace(stdout=json.dumps({"items": items}))

    def resource(kind, name):
        if kind == "storageclass":
            return {"provisioner": "k8s.io/minikube-hostpath"}
        if kind == "NetworkPolicy":
            return {"kind": kind, "metadata": {"name": name}}
        return None

    monkeypatch.setattr(helm_deploy, "ROOT", ROOT)
    monkeypatch.setattr(helm_deploy, "CHART", ROOT / snapshot.CHART)
    discoveries = []

    def ollama_ip():
        discoveries.append(True)
        return "192.168.49.1"

    target = SimpleNamespace(kubectl=kubectl, object=resource, ollama_ip=ollama_ip)
    values = []
    if host_ollama:
        path = tmp_path / "host.yaml"
        path.write_text("model:\n  ollamaBaseUrl: http://host.minikube.internal:11434\n")
        values.append(str(path))
    args = SimpleNamespace(values=values, release="local-review", namespace="test")
    with pytest.raises(ValueError, match="Existing NetworkPolicy.*not owned"):
        helm_deploy.install(target, args)
    assert bool(discoveries) is host_ollama
