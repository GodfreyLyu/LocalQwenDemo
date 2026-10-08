"""Each deployment regression uses private local state."""

import pytest


@pytest.fixture(autouse=True)
def isolated_minikube_state_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_QWEN_STATE_HOME", str(tmp_path))
