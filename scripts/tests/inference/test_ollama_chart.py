"""GPU admission failures and independent Helm storage/scheduling contracts."""

import json
import subprocess
import threading
from dataclasses import replace

import pytest
import yaml

from scripts.tests.support.ollama import API, CONFIG, runtime
from scripts.tests.support.paths import ROOT

CHART = ROOT / "deploy/helm/local-ollama"


def render(*settings):
    command = ["helm", "template", "review-ollama", str(CHART), "-n", "local-inference"]
    for setting in settings:
        command += ["--set", setting]
    docs = yaml.safe_load_all(subprocess.check_output(command, text=True))
    return {(d["kind"], d["metadata"]["name"]): d for d in docs if d}


@pytest.mark.integration
@pytest.mark.requires_helm
def test_independent_chart_retains_models_and_serializes_gpu_updates():
    resources = render()
    deployment = resources["Deployment", "review-ollama"]["spec"]
    assert deployment["replicas"] == 1
    assert deployment["strategy"] == {"type": "Recreate"}
    pod = deployment["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"]
    assert not pod["automountServiceAccountToken"]
    assert not any("hostPath" in v for v in pod["volumes"])
    container = pod["containers"][0]
    for section in ("requests", "limits"):
        assert container["resources"][section]["devic.es/dri"] == 1
    for probe in ("startupProbe", "readinessProbe"):
        assert container[probe]["httpGet"] == {"path": "/ready", "port": "health"}
    assert container["livenessProbe"]["httpGet"]["path"] == "/live"
    pvc = resources["PersistentVolumeClaim", "review-ollama-models"]
    assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    service = resources["Service", "review-ollama"]["spec"]
    assert service["selector"] == deployment["selector"]["matchLabels"]
    assert service["type"] == "ClusterIP"
    assert [p["port"] for p in service["ports"]] == [11434]
    hook = resources["Pod", "review-ollama-gpu-test"]
    assert hook["metadata"]["annotations"]["helm.sh/hook"] == "test"
    assert "devic.es/dri" not in json.dumps(hook)


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_retained_claim_and_config_change_rollout():
    before = render()
    after = render("persistence.existingClaim=retained", "model.context=2048")
    assert not any(kind == "PersistentVolumeClaim" for kind, _ in after)
    old = before["Deployment", "review-ollama"]["spec"]["template"]
    new = after["Deployment", "review-ollama"]["spec"]["template"]
    assert old["metadata"]["annotations"] != new["metadata"]["annotations"]
    assert new["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "retained"


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
@pytest.mark.parametrize(
    "setting",
    [
        "model.digest=latest",
        "model.context=0",
        "replicas=2",
        "startupTimeoutSeconds=300",
        "gpu.resourceName=gpu",
    ],
)
def test_invalid_or_unsafe_configuration_rejected(setting):
    result = subprocess.run(
        ["helm", "template", "test", str(CHART), "--set", setting], capture_output=True
    )
    assert result.returncode != 0


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.parametrize(
    "profiles",
    [
        [],
        ["values-minikube.yaml"],
        ["values-minikube.yaml", "values-minikube-ollama.yaml"],
    ],
)
def test_application_cluster_egress_is_scoped_to_the_inference_release(profiles):
    import validation.helm as validate_helm

    chart = ROOT / "deploy/helm/local-review"
    docs = validate_helm.render(
        ROOT,
        [chart / name for name in profiles],
        settings=["networkPolicy.enabled=true"],
    )
    resources = validate_helm.validate_resources(docs)
    config = resources["ConfigMap", "review-config"]["data"]
    assert "MODEL_BACKEND" not in config
    assert (
        config["OLLAMA_BASE_URL"] == "http://review-ollama.local-inference.svc.cluster.local:11434"
    )
    rules = resources["NetworkPolicy", "review-backend"]["spec"]["egress"]
    rule = next(r for r in rules if any(p["port"] == 11434 for p in r["ports"]))
    assert rule["to"] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "local-inference"}
            },
            "podSelector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "local-ollama",
                    "app.kubernetes.io/instance": "review-ollama",
                }
            },
        }
    ]


@pytest.mark.integration
@pytest.mark.requires_helm
def test_review_chart_passes_model_options_and_rejects_oversubscribed_context():
    chart = ROOT / "deploy/helm/local-review"
    command = [
        "helm",
        "template",
        "local-review",
        str(chart),
        "--set",
        "model.ollamaModel=llama3.2:1b",
        "--set",
        "model.contextTokens=8192",
        "--set-json",
        "model.temperature=0.1",
        "--set",
        "model.ollamaModelDigest=",
    ]
    docs = list(yaml.safe_load_all(subprocess.check_output(command, text=True)))
    data = next(
        d["data"]
        for d in docs
        if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "review-config"
    )
    assert data["OLLAMA_MODEL"] == "llama3.2:1b"
    assert data["MODEL_CONTEXT_TOKENS"] == "8192"
    assert data["MODEL_TEMPERATURE"] == "0.1"
    assert "OLLAMA_MODEL_DIGEST" not in data
    assert "MODEL_ID" not in data and "MODEL_REVISION" not in data
    result = subprocess.run(command + ["--set", "model.contextTokens=2048"], capture_output=True)
    assert result.returncode != 0


@pytest.mark.integration
@pytest.mark.requires_helm
def test_model_bootstrap_can_be_disabled_without_any_model_loading():
    api = API()
    api.available = api.resident = False
    runtime.prepare(api, replace(CONFIG, bootstrap=False), threading.Event())
    assert api.calls == [("/api/version", None)]
    resources = render("model.bootstrap=false")
    assert resources["ConfigMap", "review-ollama-config"]["data"]["MODEL_BOOTSTRAP"] == "false"
    assert not any(kind in {"Job", "Pod"} for kind, _ in resources)
