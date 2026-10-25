"""The consolidated workload retains release ownership, GPU configuration and model data."""

import json
import subprocess

import pytest
import yaml

from scripts.tests.support.paths import ROOT

CHART = ROOT / "deploy/helm/local-ollama"


def render(*settings):
    command = ["helm", "template", "review-ollama", str(CHART), "-n", "local-inference"]
    for setting in settings:
        command += ["--set", setting]
    return {
        (d["kind"], d["metadata"]["name"]): d
        for d in yaml.safe_load_all(subprocess.check_output(command, text=True))
        if d
    }


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_inference_retains_models_and_owns_both_processes_in_one_image():
    resources = render()
    deployment = resources["Deployment", "review-ollama"]["spec"]
    assert deployment["replicas"] == 1 and deployment["strategy"] == {"type": "Recreate"}
    pod = deployment["template"]["spec"]
    assert pod["nodeSelector"]["kubernetes.io/arch"] == "arm64"
    assert pod["securityContext"]["runAsUser"] == 10001
    assert not pod["automountServiceAccountToken"]
    assert len(pod["containers"]) == 1
    container = pod["containers"][0]
    assert container["image"] == "review-inference:local"
    for field in ("requests", "limits"):
        assert container["resources"][field]["devic.es/dri"] == 1
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == "review-ollama-models"
    assert not any("secretRef" in env for env in container["envFrom"])
    pvc = resources["PersistentVolumeClaim", "review-ollama-models"]
    assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    service = resources["Service", "review-ollama"]["spec"]
    assert service["selector"] == deployment["selector"]["matchLabels"]
    assert {p["port"] for p in service["ports"]} == {9091, 11435}
    assert 11434 not in {p["containerPort"] for p in container["ports"]}
    test = resources["Pod", "review-ollama-gpu-test"]
    assert "devic.es/dri" not in json.dumps(test)
    assert test["spec"]["containers"][0]["command"] == ["python3", "-m", "llm_service.verify"]
    dockerfile = (ROOT / "inference-service/Dockerfile").read_text()
    assert "OLLAMA_HOST=127.0.0.1:11434" in dockerfile
    assert "ollama-vulkan-0.24.0-4.fc45" in dockerfile
    assert "mesa-vulkan-drivers-25.3.6-102.fc44" in dockerfile
    assert "virtio_icd.aarch64.json" in dockerfile
    assert 'ENTRYPOINT ["llm-service"]' in dockerfile


@pytest.mark.integration
@pytest.mark.requires_helm
def test_inference_claim_can_be_adopted_explicitly_without_recreating_storage():
    resources = render("persistence.existingClaim=retained")
    assert not any(kind == "PersistentVolumeClaim" for kind, _ in resources)
    pod = resources["Deployment", "review-ollama"]["spec"]["template"]["spec"]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == "retained"


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.parametrize(
    "setting",
    [
        "model.digest=latest",
        "model.context=0",
        "replicas=2",
        "startupTimeoutSeconds=300",
        "generation.maxOutputTokens=5000",
        "model.bootstrap=false",
        "gpu.resourceName=gpu",
    ],
)
def test_invalid_configuration_and_obsolete_bootstrap_switch_are_rejected(setting):
    result = subprocess.run(
        ["helm", "template", "test", str(CHART), "--set", setting], capture_output=True
    )
    assert result.returncode != 0


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_backend_egress_targets_thrift_and_inference_accepts_only_its_clients():
    from validation.helm import render as render_app
    from validation.resources import validate_resources

    app = validate_resources(render_app(ROOT))
    data = app["ConfigMap", "review-config"]["data"]
    assert data["THRIFT_HOST"] == "review-ollama.local-inference.svc.cluster.local"
    assert not any(k.startswith("OLLAMA_") for k in data)
    rules = app["NetworkPolicy", "review-backend"]["spec"]["egress"]
    assert not any(p["port"] == 11434 for r in rules for p in r["ports"])
    rule = next(r for r in rules if any(p["port"] == 9091 for p in r["ports"]))
    peer = rule["to"][0]
    assert peer["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "local-inference"
    }
    assert peer["podSelector"]["matchLabels"]["app.kubernetes.io/instance"] == "review-ollama"
    inference = render()["NetworkPolicy", "review-ollama"]["spec"]
    assert inference["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 9091}]
    assert inference["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "local-review-demo"
    }
