"""Real generated clients/processors over TCP; model execution is a controlled double."""

import socket
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from llm_contract.generated import LlmService
from llm_contract.generated.ttypes import GenerationOptions, LlmError, Message
from llm_contract.generated.ttypes import GenerationState as State
from llm_service.handler import InferenceHandler
from llm_service.server import wire
from thrift.transport import TSocket

from .support import connection, request, terminal


def test_generic_request_and_multiple_clients_share_a_retained_result(service):
    port, handler, model, _ = service
    current = request(handler)
    with connection(port) as client:
        info = client.getModelInfo()
        assert info.defaults.max_output_tokens == 128
        assert info.limits.active_generations == 1
        client.startGeneration(current)
    result = terminal(port, handler, current)
    assert result.state == State.SUCCEEDED
    assert result.result.content == "Bonjour"
    assert result.result.usage.output_tokens == 2
    assert result.result.model_revision == info.model_revision
    assert result.result.finish_reason == "stop"
    with connection(port) as second:
        assert second.startGeneration(current) == result
    assert model.calls == [current.request_id]
    current.messages = [Message("user", "Different")]
    with connection(port) as second, pytest.raises(LlmError) as error:
        second.startGeneration(current)
    assert error.value.code == "idempotency_conflict"


def test_concurrent_duplicates_execute_once_and_capacity_is_bounded(service):
    port, handler, model, settings = service
    model.block = True
    first = request(handler)

    def start(current):
        with connection(port) as client:
            return client.startGeneration(current)

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(start, [first] * 8))
    assert model.entered.wait(1)
    second = request(handler)
    start(second)
    with connection(port) as client, pytest.raises(LlmError) as error:
        client.startGeneration(request(handler))
    assert error.value.code == "overloaded"
    with connection(port) as client:
        health = client.getHealth()
    assert (health.active, health.pending) == (1, settings.pending_generations)
    model.release.set()
    terminal(port, handler, first)
    terminal(port, handler, second)
    assert model.peak == 1 and len(model.calls) == 2


def test_cancel_waits_for_native_exit_and_queued_cancel_never_executes(service):
    port, handler, model, _ = service
    model.block = True
    model.delay_exit.clear()
    first, queued = request(handler), request(handler)
    with connection(port) as client:
        client.startGeneration(first)
        assert model.entered.wait(1)
        client.startGeneration(queued)
        assert (
            client.cancelGeneration(handler.instance_id, queued.request_id).state == State.CANCELLED
        )
        snapshot = client.cancelGeneration(handler.instance_id, first.request_id)
        assert snapshot.state == State.CANCELLING
        assert snapshot.expires_at_ms is None
        assert client.getHealth().active == 1
    model.delay_exit.set()
    assert terminal(port, handler, first).state == State.CANCELLED
    assert model.calls == [first.request_id]


def test_deadlines_apply_to_orphaned_active_and_pending_generations(service):
    port, handler, model, _ = service
    model.block = True
    model.delay_exit.clear()
    first, queued = request(handler, deadline_ms=150), request(handler, deadline_ms=30)
    with connection(port) as client:
        client.startGeneration(first)
        assert model.entered.wait(1)
        client.startGeneration(queued)
    time.sleep(0.2)
    with connection(port) as client:
        assert client.getGeneration(handler.instance_id, first.request_id).state == State.CANCELLING
        pending = client.getGeneration(handler.instance_id, queued.request_id)
    assert pending.state == State.CANCELLED and pending.error.code == "deadline_exceeded"
    model.delay_exit.set()
    assert terminal(port, handler, first).error.code == "deadline_exceeded"
    assert queued.request_id not in model.calls


def test_results_do_not_evict_each_other_and_registry_rejects_until_expiry(service):
    port, handler, model, settings = service
    settings.retained_results = 2
    settings.result_ttl_seconds = 0.3
    first = request(handler)
    with connection(port) as client:
        client.startGeneration(first)
    terminal(port, handler, first)
    second = request(handler)
    with connection(port) as client:
        client.startGeneration(second)
    terminal(port, handler, second)
    with connection(port) as client:
        assert (
            client.getGeneration(handler.instance_id, first.request_id).result.content == "Bonjour"
        )
        with pytest.raises(LlmError) as error:
            client.startGeneration(request(handler))
        assert error.value.code == "retention_full"
    time.sleep(0.32)
    with connection(port) as client:
        with pytest.raises(LlmError) as error:
            client.getGeneration(handler.instance_id, first.request_id)
        assert error.value.code == "not_found"
        # After expiry the same ID is a new operation, as documented.
        client.startGeneration(first)
    terminal(port, handler, first)
    assert model.calls.count(first.request_id) == 2


def test_tcp_disconnect_after_send_does_not_duplicate_or_cancel_work(service):
    port, handler, model, _ = service
    current = request(handler)
    sock = TSocket.TSocket("127.0.0.1", port)
    transport, protocol = wire(sock)
    transport.open()
    LlmService.Client(protocol).send_startGeneration(current)
    transport.close()  # discard acceptance response
    assert model.entered.wait(1)
    snapshot = terminal(port, handler, current)
    assert snapshot.state == State.SUCCEEDED
    with connection(port) as client:
        assert client.startGeneration(current).state == State.SUCCEEDED
    assert model.calls == [current.request_id]


def test_restart_fences_old_ids_and_has_no_persisted_results(service):
    port, handler, model, settings = service
    current = request(handler)
    with connection(port) as client:
        client.startGeneration(current)
    terminal(port, handler, current)
    replacement = InferenceHandler(model, settings)
    try:
        assert replacement.instance_id != handler.instance_id
        with pytest.raises(LlmError) as error:
            replacement.getGeneration(handler.instance_id, current.request_id)
        assert error.value.code == "instance_changed"
        with pytest.raises(LlmError) as error:
            replacement.getGeneration(replacement.instance_id, current.request_id)
        assert error.value.code == "not_found"
    finally:
        replacement.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"messages": []},
        {"messages": [Message("tool", "hi")]},
        {"messages": [Message("user", "x" * 64001)]},
        {"deadline_ms": 3001},
        {"options": GenerationOptions(max_output_tokens=513)},
        {"options": GenerationOptions(temperature=float("nan"))},
        {"options": GenerationOptions(top_p=0)},
        {"options": GenerationOptions(seed=-1)},
    ],
)
def test_service_enforces_limits_independently_of_clients(service, overrides):
    port, handler, model, _ = service
    with connection(port) as client, pytest.raises(LlmError):
        client.startGeneration(request(handler, **overrides))
    assert model.calls == []


def test_shutdown_rejects_new_work_and_waits_for_active_exit(service):
    port, handler, model, _ = service
    model.block = True
    current = request(handler)
    with connection(port) as client:
        client.startGeneration(current)
        assert model.entered.wait(1)
    handler.close(2)
    assert not handler.worker.is_alive()
    with connection(port) as client:
        assert client.getHealth().draining
        assert (
            client.getGeneration(handler.instance_id, current.request_id).error.code == "shutdown"
        )
        with pytest.raises(LlmError) as error:
            client.startGeneration(request(handler))
    assert error.value.code == "unavailable"


def test_malformed_frame_is_closed_without_logging_content(service, capsys):
    port, _, _, _ = service
    with socket.create_connection(("127.0.0.1", port)) as peer:
        peer.sendall(b"\x7f\xff\xff\xffPRIVATE_CONTENT")
        peer.settimeout(1)
        try:
            assert peer.recv(1) == b""
        except ConnectionResetError:
            pass
    assert "PRIVATE" not in capsys.readouterr().err


def test_unprovable_native_exit_never_becomes_a_terminal_cancellation(service, monkeypatch):
    from llm_service.errors import ExecutionUncertain

    port, handler, model, _ = service

    def cannot_confirm(*_):
        raise ExecutionUncertain()

    monkeypatch.setattr(model, "generate", cannot_confirm)
    current = request(handler)
    with connection(port) as client:
        client.startGeneration(current)
    handler.worker.join(1)
    with connection(port) as client:
        snapshot = client.getGeneration(handler.instance_id, current.request_id)
        assert snapshot.state == State.CANCELLING and snapshot.expires_at_ms is None
        assert client.getHealth().draining
        with pytest.raises(LlmError):
            client.startGeneration(request(handler))
