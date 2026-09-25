"""Keep deployment CLI tests away from real user-level state."""

import pytest


@pytest.fixture(autouse=True)
def isolated_minikube_state_root(monkeypatch, tmp_path, request):
    if request.node.path.name.startswith("test_minikube"):
        monkeypatch.setenv("LOCAL_QWEN_STATE_HOME", str(tmp_path))
