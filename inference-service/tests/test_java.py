"""Packaged Java SDK and an independent Maven consumer use real Python Thrift over TCP."""

import importlib.util
import os
import queue
import shutil
import socketserver
import subprocess
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from llm_contract.generated import LlmService
from llm_contract.generated.ttypes import (
    GenerationOptions,
    GenerationRequest,
    GenerationResult,
    GenerationSnapshot,
    GenerationState,
    Health,
    Limits,
    LlmError,
    Message,
    ModelInfo,
    TokenUsage,
)
from llm_service.server import wire
from thrift.protocol import TBinaryProtocol
from thrift.transport import TSocket, TTransport

from .support import connection, terminal

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_JAVA_INTEROP") != "1",
    reason="Run scripts/checks/inference.sh for Java gate",
)


@pytest.fixture(scope="module")
def java_tools():
    # Load the existing build entry point without requiring the checkout root on sys.path.
    path = Path(__file__).resolve().parents[2] / "scripts/java_thrift.py"
    spec = importlib.util.spec_from_file_location("java_thrift", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def java_classpath(java_tools):
    return java_tools.compile_example()


def run_java(classpath, main, *args):
    result = subprocess.run(
        ["java", "-cp", classpath, main, *map(str, args)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_java_and_python_clients_observe_one_execution(service, java_classpath):
    port, handler, model, settings = service
    settings.result_ttl_seconds = 3
    request_id = str(uuid.uuid4())
    output = run_java(java_classpath, "Generate", "127.0.0.1", port, request_id)
    assert "Bonjour" in output and "finish_reason=stop" in output
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


class ControlledHandler:
    """No model process: preserve wire values and control task transitions deterministically."""

    instance_id = "python-test-instance"

    def __init__(self, mode):
        self.mode = mode
        self.requests = []
        self.cancelled = []
        self.overlap = threading.Barrier(8)

    def getModelInfo(self):
        return ModelInfo(
            self.instance_id,
            "test-model",
            "test-revision",
            GenerationOptions(),
            Limits(1, 8, 128, 600000, 300000, 64000, 8192, 1024),
        )

    def getHealth(self):
        return Health(True, False, 0, 0)

    def startGeneration(self, request):
        assert request.instance_id == self.instance_id
        assert request.deadline_ms == 3000
        assert request.messages[0].role == "user"
        self.requests.append(request)
        return GenerationSnapshot(request.request_id, self.instance_id, GenerationState.QUEUED)

    def getGeneration(self, instance_id, request_id):
        assert instance_id == self.instance_id
        if request_id == "missing":
            raise LlmError("not_found", "No retained task", False)
        if self.mode == "concurrent":
            self.overlap.wait(timeout=5)
        state = (
            GenerationState.CANCELLED
            if request_id in self.cancelled
            else GenerationState.RUNNING
            if self.mode == "lost"
            else GenerationState.SUCCEEDED
        )
        result = GenerationResult(
            "Bonjour", "test-model", "test-revision", TokenUsage(12, 2), "stop"
        )
        return GenerationSnapshot(
            request_id,
            self.instance_id,
            state,
            result if state == GenerationState.SUCCEEDED else None,
            expires_at_ms=4102444800000,
        )

    def cancelGeneration(self, instance_id, request_id):
        assert instance_id == self.instance_id
        self.cancelled.append(request_id)
        return GenerationSnapshot(request_id, instance_id, GenerationState.CANCELLING)


class ObservedConnection(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        sock = TSocket.TSocket()
        sock.setHandle(self.request)
        transport, protocol = wire(sock)
        try:
            # Serialize with the generated Python processor, then optionally discard only the
            # submission response to prove recovery after an actually accepted TCP request.
            output = TTransport.TMemoryBuffer()
            self.server.processor.process(
                protocol, TBinaryProtocol.TBinaryProtocol(output, strictRead=True, strictWrite=True)
            )
            response = output.getvalue()
            method, _, _ = TBinaryProtocol.TBinaryProtocol(
                TTransport.TMemoryBuffer(response), strictRead=True
            ).readMessageBegin()
            if not (self.server.handler.mode == "lost" and method == "startGeneration"):
                transport.write(response)
                transport.flush()
            assert self.request.recv(1) == b"", "Java caller did not close the RPC connection"
            self.server.completions.put(None)
        except Exception as error:
            self.server.completions.put(error)
        finally:
            transport.close()


@contextmanager
def controlled_server(mode):
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        request_queue_size = 32

    with Server(("127.0.0.1", 0), ObservedConnection) as server:
        server.handler = ControlledHandler(mode)
        server.processor = LlmService.Processor(server.handler)
        server.completions = queue.Queue()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(3)


def assert_closed(server, count):
    for _ in range(count):
        error = server.completions.get(timeout=6)
        assert error is None, repr(error)


@pytest.mark.parametrize("mode,connections", [("lifecycle", 7), ("lost", 4), ("concurrent", 10)])
def test_sdk_python_wire_semantics_and_cleanup(java_classpath, mode, connections):
    with controlled_server(mode) as server:
        output = run_java(
            java_classpath, "demo.llm.client.InteropProbe", server.server_address[1], mode
        )
        assert f"ok={mode}" in output
        assert_closed(server, connections)
        if mode != "concurrent":
            # In particular, a lost response must not result in a second startGeneration call.
            assert len(server.handler.requests) == 1
            assert server.handler.requests[0].options.seed == 2**63 - 1
        if mode == "lifecycle":
            assert server.handler.cancelled == ["cancel-me"]


def test_independent_maven_consumer_uses_only_installed_sdk(java_classpath, java_tools, tmp_path):
    # Build outside the checkout: no parent reactor, generated source tree, or SDK test classes.
    consumer = tmp_path / "consumer"
    shutil.copytree(java_tools.CONSUMER, consumer, ignore=shutil.ignore_patterns("target"))
    java_tools.compile_consumer(consumer)
    with controlled_server("consumer") as server:
        result = java_tools.maven(
            consumer / "pom.xml",
            "exec:java",
            f"-Dexec.args=127.0.0.1 {server.server_address[1]} cancel",
            capture_output=True,
        )
        output = result.stdout
        assert "Bonjour" in output
        assert "state=SUCCEEDED" in output and "state=CANCELLED" in output
        assert len(server.handler.requests) == 2
        assert server.handler.cancelled == [server.handler.requests[1].request_id]
        assert_closed(server, 7)
