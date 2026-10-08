"""GPU admission failures and independent Helm storage/scheduling contracts."""

import importlib.util
import sys

from scripts.tests.support.paths import ROOT

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
