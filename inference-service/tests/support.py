import threading
import time
import uuid
from contextlib import contextmanager

import pytest
from llm_contract.generated import LlmService
from llm_contract.generated.ttypes import (
    GenerationOptions,
    GenerationRequest,
    GenerationResult,
    GenerationState,
    Message,
    TokenUsage,
)
from llm_service.config import Settings
from llm_service.handler import InferenceHandler
from llm_service.server import RpcServer, wire
from thrift.transport import TSocket


class Model:
    quantization, device = "Q4_K_M", "gpu"

    def __init__(self, settings):
        self.settings = settings
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.delay_exit = threading.Event()
        self.delay_exit.set()
        self.block = False
        self.active = self.peak = 0

    def ready(self):
        return True

    def generate(self, request, stop):
        self.calls.append(request.request_id)
        self.active += 1
        self.peak = max(self.active, self.peak)
        self.entered.set()
        try:
            while self.block and not self.release.wait(0.01) and not stop.is_set():
                pass
            assert self.delay_exit.wait(3)
            return GenerationResult(
                "Bonjour",
                self.settings.model_name,
                self.settings.model_digest,
                TokenUsage(12, 2),
                "stop",
                20,
                5,
            )
        finally:
            self.active -= 1


@contextmanager
def connection(port):
    sock = TSocket.TSocket("127.0.0.1", port)
    sock.setTimeout(1000)
    transport, protocol = wire(sock)
    transport.open()
    try:
        yield LlmService.Client(protocol)
    finally:
        transport.close()


def request(handler, **overrides):
    values = dict(
        request_id=str(uuid.uuid4()),
        instance_id=handler.instance_id,
        messages=[Message("system", "Translate to French."), Message("user", "Hello")],
        options=GenerationOptions(max_output_tokens=16, temperature=0, seed=123),
        deadline_ms=2000,
    )
    values.update(overrides)
    return GenerationRequest(**values)


def terminal(port, handler, current):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        with connection(port) as client:
            result = client.getGeneration(handler.instance_id, current.request_id)
        if result.state in {
            GenerationState.SUCCEEDED,
            GenerationState.CANCELLED,
            GenerationState.FAILED,
        }:
            return result
        time.sleep(0.01)
    pytest.fail("Generation did not exit")


@pytest.fixture
def service():
    settings = Settings(
        _env_file=None,
        max_deadline_seconds=3,
        result_ttl_seconds=0.2,
        pending_generations=1,
        retained_results=4,
    )
    model = Model(settings)
    handler = InferenceHandler(model, settings)
    with RpcServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield server.server_address[1], handler, model, settings
        finally:
            model.release.set()
            model.delay_exit.set()
            handler.close(3)
            server.shutdown()
            thread.join(3)
