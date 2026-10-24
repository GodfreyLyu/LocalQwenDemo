"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import shutil
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
def test_external_volume_and_volume_retention():
    before = validate_helm.validate_resources(
        validate_helm.render(ROOT, [ROOT / validate_helm.MINIKUBE_VALUES])
    )
    after = validate_helm.validate_resources(
        validate_helm.render(
            ROOT,
            [ROOT / validate_helm.MINIKUBE_VALUES],
            settings=[
                "volumePermissions.enabled=false",
                "persistence.history.existingClaim=old-history",
            ],
        )
    )
    new = after["Deployment", "review-backend"]["spec"]["template"]
    assert [i["name"] for i in new["spec"]["initContainers"]] == ["initialize-users"]
    assert ("PersistentVolumeClaim", "review-history") not in after
    assert all(
        r["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
        and r["metadata"]["annotations"]["argocd.argoproj.io/sync-options"]
        == "Prune=false,Delete=false"
        for (kind, _), r in before.items()
        if kind == "PersistentVolumeClaim"
    )


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
@pytest.mark.parametrize(
    "change,expected", [("config", "review-backend"), ("nginx", "review-frontend")]
)
def test_configuration_changes_roll_only_the_consuming_workload(tmp_path, change, expected):
    chart = tmp_path / snapshot.CHART
    shutil.copytree(ROOT / snapshot.CHART, chart)
    values = [tmp_path / validate_helm.MINIKUBE_VALUES]
    before = validate_helm.validate_resources(validate_helm.render(tmp_path, values))
    settings = []
    if change == "config":
        settings.append("config.queueCapacity=6")
    else:
        nginx = chart / "files/nginx.conf"
        nginx.write_text(nginx.read_text() + "\n# updated frontend configuration\n")
    after = validate_helm.validate_resources(validate_helm.render(tmp_path, values, settings))
    changed = {
        name
        for (kind, name), resource in before.items()
        if kind == "Deployment"
        and resource["spec"]["template"] != after[kind, name]["spec"]["template"]
    }
    assert changed == {expected}


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_retired_model_cache_values_do_not_change_resources():
    values = [ROOT / validate_helm.MINIKUBE_VALUES]
    before = validate_helm.validate_resources(validate_helm.render(ROOT, values))
    after = validate_helm.validate_resources(
        validate_helm.render(
            ROOT,
            values,
            settings=[
                "persistence.modelCache.size=12Gi",
                "persistence.modelCache.existingClaim=retained-model-cache",
            ],
        )
    )
    assert after == before
    assert {name for kind, name in after if kind == "PersistentVolumeClaim"} == {
        "review-history",
        "review-dynamodb",
    }


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
