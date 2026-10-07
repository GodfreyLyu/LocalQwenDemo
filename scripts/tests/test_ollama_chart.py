"""GPU admission failures and independent Helm storage/scheduling contracts."""

import importlib.util
import json
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/local-ollama"
spec = importlib.util.spec_from_file_location(
    "ollama_runtime", ROOT / "deploy/images/ollama-vulkan/runtime.py"
)
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)
CONFIG = runtime.Config("qwen3:1.7b", "a" * 64, 4096, 2, True, 30)


class API:
    def __init__(self):
        self.model = {
            "name": CONFIG.model,
            "digest": CONFIG.digest,
            "size": 100,
            "size_vram": 100,
        }
        self.available = True
        self.resident = True
        self.calls = []
        self.response = {
            "model": CONFIG.model,
            "done": True,
            "eval_count": 1,
            "message": {"content": "Ready"},
        }

    def call(self, path, payload=None, timeout=5):
        self.calls.append((path, payload))
        if path == "/api/tags":
            return {"models": [self.model] if self.available else []}
        if path == "/api/ps":
            return {"models": [self.model] if self.resident else []}
        if path == "/api/chat":
            return self.response
        if path == "/api/pull":
            self.available = True
        return {}


@pytest.mark.parametrize("vram", [0, 1, 99, None, "100", True])
def test_cpu_partial_and_unverifiable_offload_fail(vram):
    api = API()
    api.model["size_vram"] = vram
    with pytest.raises(RuntimeError, match="GPU loading"):
        runtime.warmup(api, CONFIG)


def test_model_digest_mismatch_never_generates_or_replaces_model():
    api = API()
    api.model["digest"] = "b" * 64
    with pytest.raises(RuntimeError, match="digest"):
        runtime.prepare(api, CONFIG, threading.Event())
    assert not any(path in ("/api/chat", "/api/pull") for path, _ in api.calls)


@pytest.mark.parametrize(
    "override",
    [
        {"done": False},
        {"eval_count": 0},
        {"message": {"content": ""}},
        {"model": "other"},
    ],
)
def test_incomplete_generation_cannot_pass_gpu_gate(override):
    api = API()
    api.response.update(override)
    with pytest.raises(RuntimeError, match="warmup"):
        runtime.warmup(api, CONFIG)


def test_first_start_pulls_but_pvc_restart_reuses_model():
    api = API()
    api.available = False
    runtime.prepare(api, CONFIG, threading.Event())
    runtime.prepare(api, CONFIG, threading.Event())
    assert sum(path == "/api/pull" for path, _ in api.calls) == 1
    payloads = [payload for path, payload in api.calls if path == "/api/chat"]
    assert not payloads  # Bootstrap installs only; Review selects and loads its own model.


def test_offline_missing_model_never_downloads(monkeypatch):
    api = API()
    api.available = False
    clock = iter([0, 1, 100])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="waiting for the model"):
        runtime.prepare(api, replace(CONFIG, pull=False), threading.Event())
    assert not any(path == "/api/pull" for path, _ in api.calls)


def test_resident_gpu_state_is_rechecked_after_success():
    api = API()
    runtime.warmup(api, CONFIG)
    api.resident = False
    with pytest.raises(runtime.ModelNotLoaded):
        runtime.gpu_state(api, CONFIG)
    api.resident = True
    api.model["size_vram"] = 0
    with pytest.raises(RuntimeError, match="GPU loading"):
        runtime.gpu_state(api, CONFIG)


def render(*settings):
    command = ["helm", "template", "review-ollama", str(CHART), "-n", "local-inference"]
    for setting in settings:
        command += ["--set", setting]
    docs = yaml.safe_load_all(subprocess.check_output(command, text=True))
    return {(d["kind"], d["metadata"]["name"]): d for d in docs if d}


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


def test_retained_claim_and_config_change_rollout():
    before = render()
    after = render("persistence.existingClaim=retained", "model.context=2048")
    assert not any(kind == "PersistentVolumeClaim" for kind, _ in after)
    old = before["Deployment", "review-ollama"]["spec"]["template"]
    new = after["Deployment", "review-ollama"]["spec"]["template"]
    assert old["metadata"]["annotations"] != new["metadata"]["annotations"]
    assert new["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "retained"


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


@pytest.mark.parametrize(
    "profiles",
    [
        [],
        ["values-minikube.yaml"],
        ["values-minikube.yaml", "values-minikube-ollama.yaml"],
    ],
)
def test_application_cluster_egress_is_scoped_to_the_inference_release(profiles):
    sys.path.insert(0, str(ROOT / "scripts"))
    import validate_helm

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


def test_model_bootstrap_can_be_disabled_without_any_model_loading():
    api = API()
    api.available = api.resident = False
    runtime.prepare(api, replace(CONFIG, bootstrap=False), threading.Event())
    assert api.calls == [("/api/version", None)]
    resources = render("model.bootstrap=false")
    assert resources["ConfigMap", "review-ollama-config"]["data"]["MODEL_BOOTSTRAP"] == "false"
    assert not any(kind in {"Job", "Pod"} for kind, _ in resources)


@pytest.mark.parametrize(
    "resident,available,vram", [(False, False, 0), (True, True, 0), (True, True, 50)]
)
def test_readiness_does_not_depend_on_bootstrap_model_residency(resident, available, vram):
    import io
    from types import SimpleNamespace

    api = API()
    api.resident, api.available = resident, available
    api.model["size_vram"] = vram
    prepared = threading.Event()
    prepared.set()
    health = runtime.handler(api, SimpleNamespace(poll=lambda: None), prepared)
    request = health.__new__(health)
    request.path = "/ready"
    request.wfile = io.BytesIO()
    statuses = []
    request.send_response = statuses.append
    request.send_header = lambda *args: None
    request.end_headers = lambda: None
    request.do_GET()
    assert statuses == [200]
    assert api.calls == [("/api/version", None)]


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
