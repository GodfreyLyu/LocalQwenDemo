"""A real service-process replacement loses its registry and fences old request IDs."""

import select
import subprocess
import sys
import time

import pytest
from llm_contract.generated.ttypes import GenerationState, LlmError

from .support import connection, request

PROGRAM = """
import sys
from types import SimpleNamespace
from llm_contract.generated.ttypes import GenerationResult, TokenUsage
from llm_service.config import Settings
from llm_service.handler import InferenceHandler
from llm_service.server import RpcServer
settings = Settings(_env_file=None, result_ttl_seconds=60)
model = SimpleNamespace(quantization=None, device='cpu', ready=lambda: True,
    generate=lambda request, stop: GenerationResult('Bonjour', settings.model_name,
        settings.model_digest, TokenUsage(12, 2), 'stop'))
handler = InferenceHandler(model, settings)
with RpcServer(('127.0.0.1', int(sys.argv[1])), handler) as server:
    print(server.server_address[1], flush=True)
    server.serve_forever(poll_interval=0.01)
"""


def start_service(port=0):
    process = subprocess.Popen(
        [sys.executable, "-c", PROGRAM, str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "Service did not open RPC"
        return process, int(process.stdout.readline())
    except BaseException:
        process.terminate()
        process.wait(3)
        raise


def test_process_restart_fences_ids_without_reexecuting_unknown_work():
    process, port = start_service()
    try:
        with connection(port) as client:
            previous = client.getModelInfo()
            current = request(previous)
            snapshot = client.startGeneration(current)
            while snapshot.state != GenerationState.SUCCEEDED:
                time.sleep(0.01)
                snapshot = client.getGeneration(previous.instance_id, current.request_id)
        process.kill()
        process.wait(3)
        process, replacement_port = start_service(port)
        assert replacement_port == port
        with connection(port) as client:
            replacement = client.getModelInfo()
            assert replacement.instance_id != previous.instance_id
            with pytest.raises(LlmError) as error:
                client.getGeneration(previous.instance_id, current.request_id)
            assert error.value.code == "instance_changed"
            with pytest.raises(LlmError) as error:
                client.getGeneration(replacement.instance_id, current.request_id)
            assert error.value.code == "not_found"
    finally:
        process.terminate()
        process.wait(3)
