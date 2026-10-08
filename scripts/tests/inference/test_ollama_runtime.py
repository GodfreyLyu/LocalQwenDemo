"""GPU admission failures and independent Helm storage/scheduling contracts."""

import threading
from dataclasses import replace

import pytest

from scripts.tests.support.ollama import API, CONFIG, runtime
from scripts.tests.support.paths import ROOT

CHART = ROOT / "deploy/helm/local-ollama"


@pytest.mark.component
@pytest.mark.parametrize("vram", [0, 1, 99, None, "100", True])
def test_cpu_partial_and_unverifiable_offload_fail(vram):
    api = API()
    api.model["size_vram"] = vram
    with pytest.raises(RuntimeError, match="GPU loading"):
        runtime.warmup(api, CONFIG)


@pytest.mark.component
@pytest.mark.contract
def test_model_digest_mismatch_never_generates_or_replaces_model():
    api = API()
    api.model["digest"] = "b" * 64
    with pytest.raises(RuntimeError, match="digest"):
        runtime.prepare(api, CONFIG, threading.Event())
    assert not any(path in ("/api/chat", "/api/pull") for path, _ in api.calls)


@pytest.mark.component
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


@pytest.mark.component
@pytest.mark.recovery
def test_first_start_pulls_but_pvc_restart_reuses_model():
    api = API()
    api.available = False
    runtime.prepare(api, CONFIG, threading.Event())
    runtime.prepare(api, CONFIG, threading.Event())
    assert sum(path == "/api/pull" for path, _ in api.calls) == 1
    payloads = [payload for path, payload in api.calls if path == "/api/chat"]
    assert not payloads  # Bootstrap installs only; Review selects and loads its own model.


@pytest.mark.component
def test_offline_missing_model_never_downloads(monkeypatch):
    api = API()
    api.available = False
    clock = iter([0, 1, 100])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="waiting for the model"):
        runtime.prepare(api, replace(CONFIG, pull=False), threading.Event())
    assert not any(path == "/api/pull" for path, _ in api.calls)


@pytest.mark.component
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


@pytest.mark.component
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
