"""Java and Python generated clients share the same request and retained result over TCP."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from llm_contract.generated.ttypes import GenerationOptions, GenerationRequest, Message

from .support import connection, terminal


@pytest.mark.skipif(
    os.environ.get("RUN_JAVA_INTEROP") != "1",
    reason="Run scripts/checks/inference.sh for Java gate",
)
def test_java_and_python_clients_observe_one_execution(service):
    port, handler, model, settings = service
    settings.result_ttl_seconds = 3
    root = Path(__file__).resolve().parents[2]
    request_id = str(uuid.uuid4())
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/java_thrift.py"),
            "--port",
            str(port),
            "--request-id",
            request_id,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Bonjour" in result.stdout and "finish_reason=stop" in result.stdout
    current = GenerationRequest(
        request_id,
        handler.instance_id,
        [Message("user", "Translate Hello to French.")],
        GenerationOptions(max_output_tokens=32, temperature=0),
        3000,
    )
    with connection(port) as python_client:
        assert python_client.startGeneration(current).result.content == "Bonjour"
    assert terminal(port, handler, current).result.usage.output_tokens == 2
    assert model.calls == [request_id]
