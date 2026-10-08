"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import subprocess

import pytest
import release.snapshot_impl as snapshot  # noqa: E402
import validation.helm as validate_helm  # noqa: E402

from scripts.tests.support.paths import ROOT


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_schema_rejects_multiple_workers_and_invalid_configuration():
    for setting in (
        "backend.replicas=2",
        "model.cpuThreads=0",
        "config.queueCapacity=100",
        "model.backend=invalid",
    ):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(ROOT / snapshot.CHART),
                "-f",
                str(ROOT / validate_helm.MINIKUBE_VALUES),
                "--set",
                setting,
            ],
            capture_output=True,
        )
        assert result.returncode != 0


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_config_rollout_and_volume_retention():
    before = validate_helm.validate_resources(
        validate_helm.render(ROOT, [ROOT / validate_helm.MINIKUBE_VALUES])
    )
    after = validate_helm.validate_resources(
        validate_helm.render(
            ROOT,
            [ROOT / validate_helm.MINIKUBE_VALUES],
            settings=[
                "config.queueCapacity=6",
                "volumePermissions.enabled=false",
                "persistence.history.existingClaim=old-history",
            ],
        )
    )
    old = before["Deployment", "review-backend"]["spec"]["template"]
    new = after["Deployment", "review-backend"]["spec"]["template"]
    assert (
        old["metadata"]["annotations"]["checksum/config"]
        != new["metadata"]["annotations"]["checksum/config"]
    )
    assert [i["name"] for i in new["spec"]["initContainers"]] == ["initialize-users"]
    assert ("PersistentVolumeClaim", "review-history") not in after
    assert all(
        r["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
        for (kind, _), r in before.items()
        if kind == "PersistentVolumeClaim"
    )


@pytest.mark.integration
@pytest.mark.requires_helm
def test_ollama_rule_is_an_egress_rule():
    resources = validate_helm.validate_resources(
        validate_helm.render(
            ROOT,
            settings=[
                "networkPolicy.ollamaNamespace=",
                "model.ollamaBaseUrl=http://host.minikube.internal:11434",
                "networkPolicy.ollamaHostCidr=192.168.49.1/32",
            ],
        )
    )
    policy = resources["NetworkPolicy", "review-backend"]["spec"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    assert any(
        rule.get("to") == [{"ipBlock": {"cidr": "192.168.49.1/32"}}]
        and rule["ports"] == [{"protocol": "TCP", "port": 11434}]
        for rule in policy["egress"]
    )


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.parametrize(
    "cidr", ["", "0.0.0.0/0", "192.168.1.0/24", "192.168.999.1/32", "8.8.8.8/32"]
)
def test_enabled_ollama_policy_rejects_missing_or_invalid_host(cidr):
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(ROOT / snapshot.CHART),
            "--set-string",
            "networkPolicy.ollamaHostCidr=" + cidr,
            "--set-string",
            "networkPolicy.ollamaNamespace=",
            "--set-string",
            "model.ollamaBaseUrl=http://host.minikube.internal:11434",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ollamaHostCidr" in result.stderr
