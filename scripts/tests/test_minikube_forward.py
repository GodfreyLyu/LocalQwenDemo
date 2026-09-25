"""Offline TCP/process regressions: no kubectl, cluster, account, or review operations."""

import errno
import select
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minikube_demo as d  # noqa: E402

POPEN = subprocess.Popen
SERVER = r"""
import signal, socket, sys, time
port, mode = int(sys.argv[1]), sys.argv[2]
if mode == "exit":
    print("raw-output-must-stay-private", flush=True)
    sys.exit(7)
if mode == "silent":
    sys.stdout.write("raw-output-must-stay-private")
    sys.stdout.flush()
    time.sleep(60)
if mode == "false_announcement":
    print(f"Forwarding from 127.0.0.1:{port} -> 8080", flush=True)
    time.sleep(60)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", port))
    s.listen()
except OSError:
    print("listen: address already in use; raw-output-must-stay-private", flush=True)
    sys.exit(1)
print(f"Forwarding from 127.0.0.1:{s.getsockname()[1]} -> 8080", flush=True)
while True:
    c, _ = s.accept()
    with c:
        try:
            c.sendall(b"ready")
            c.shutdown(socket.SHUT_WR)
            while c.recv(32):
                pass
        except OSError:
            pass
"""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def exchange(port):
    with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
        data = b""
        while chunk := client.recv(32):
            data += chunk
        assert data == b"ready"


@pytest.fixture
def unrelated_listener():
    """A real listener owned by the test, never by the forward() under test."""
    proc = POPEN(
        [sys.executable, "-u", "-c", SERVER, "0", "normal"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert select.select([proc.stdout], [], [], 5)[0]
        line = proc.stdout.readline().decode()
        port = int(line.split(":")[1].split()[0])
        yield proc, port
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        proc.stdout.close()


@pytest.fixture
def harness(monkeypatch):
    """Replace kubectl with a real loopback test child; track and reap only owned processes."""
    state = SimpleNamespace(processes=[], mode="normal", before_spawn=None)

    def command(*args):
        assert args[:3] == ("port-forward", "--address=127.0.0.1", "service/review-frontend")
        return [sys.executable, "-u", "-c", SERVER, args[-1].split(":")[0], state.mode]

    def spawn(*args, **kwargs):
        if state.before_spawn:
            state.before_spawn()
        proc = POPEN(*args, **kwargs)
        state.processes.append(proc)
        return proc

    monkeypatch.setattr(d, "kargs", command)
    monkeypatch.setattr(d.subprocess, "Popen", spawn)
    yield state
    for proc in state.processes:
        # A failed assertion must not leave a test subprocess behind.
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if proc.stdout:
            proc.stdout.close()


def test_free_port_probe_releases_its_socket():
    port = free_port()
    assert d.port_available(port) is True
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
        sock.listen()


def test_unknown_listener_is_rejected_and_unaffected(unrelated_listener, harness):
    proc, port = unrelated_listener
    with pytest.raises(d.DemoError, match=rf"address_in_use; errno={errno.EADDRINUSE}"):
        with d.forward("review-frontend", 8080, port):
            pytest.fail("An unrelated listener must never be used")
    assert not harness.processes
    assert proc.poll() is None
    exchange(port)


@pytest.mark.parametrize(
    ("number", "reason"),
    [
        (errno.EACCES, "permission_denied"),
        (errno.EPERM, "permission_denied"),
        (errno.EADDRNOTAVAIL, "bind_failed"),
        (errno.EMFILE, "bind_failed"),
    ],
)
def test_binding_errors_keep_only_safe_errno(monkeypatch, number, reason):
    sock = MagicMock()
    sock.__enter__.return_value = sock
    sock.bind.side_effect = OSError(number, "raw-output-must-stay-private")
    monkeypatch.setattr(d.socket, "socket", Mock(return_value=sock))
    with pytest.raises(d.DemoError) as error:
        d.port_available(8080)
    assert f"{reason}; errno={number} ({errno.errorcode[number]})" in str(error.value)
    assert "raw-output" not in str(error.value)
    assert "address_in_use" not in str(error.value)


def test_owned_forward_rebuilds_same_port_after_active_close(harness):
    port = free_port()
    with d.forward("review-frontend", 8080, port):
        exchange(port)  # Read EOF before closing: the server actively closes this connection.
    assert harness.processes[0].poll() is not None
    with socket.socket() as plain:
        with pytest.raises(OSError) as error:
            plain.bind(("127.0.0.1", port))
        assert error.value.errno == errno.EADDRINUSE
    assert d.port_available(port) is True
    with d.forward("review-frontend", 8080, port):
        exchange(port)
    assert len(harness.processes) == 2
    assert all(proc.poll() is not None and proc.stdout.closed for proc in harness.processes)


def test_race_after_probe_rejects_child_failure_without_touching_winner(harness):
    port = free_port()
    with socket.socket() as winner:

        def occupy():
            winner.bind(("127.0.0.1", port))
            winner.listen()

        harness.before_spawn = occupy
        with pytest.raises(d.DemoError, match="startup_failed.*address_in_use"):
            with d.forward("review-frontend", 8080, port):
                pytest.fail("A successful probe is not successful forwarding")
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            connection, _ = winner.accept()
            connection.close()
        assert harness.processes[0].poll() is not None


@pytest.mark.parametrize("mode", ["exit", "silent", "false_announcement"])
def test_startup_failure_is_bounded_and_output_is_withheld(harness, monkeypatch, mode):
    harness.mode = mode
    monkeypatch.setattr(d, "FORWARD_START_TIMEOUT", 0.3)
    started = time.monotonic()
    with pytest.raises(d.DemoError) as error:
        with d.forward("review-frontend", 8080, free_port()):
            pytest.fail("An unconfirmed listener must not be yielded")
    assert time.monotonic() - started < 3
    assert "raw-output" not in str(error.value)
    assert harness.processes[0].poll() is not None
    assert harness.processes[0].stdout.closed


def test_spawn_failure_keeps_safe_errno(harness, monkeypatch):
    monkeypatch.setattr(
        d.subprocess, "Popen", Mock(side_effect=OSError(errno.EACCES, "private output"))
    )
    with pytest.raises(d.DemoError, match=rf"process_start_failed; errno={errno.EACCES}"):
        with d.forward("review-frontend", 8080, free_port()):
            pytest.fail("A failed spawn cannot be ready")


def test_owned_process_exit_is_failure(harness):
    with pytest.raises(d.DemoError, match="exited unexpectedly"):
        with d.forward("review-frontend", 8080, free_port()):
            harness.processes[0].terminate()
            harness.processes[0].wait(timeout=5)


@pytest.mark.parametrize("mode", ["normal", "ignore_term"])
def test_cleanup_on_body_failure_only_stops_owned_process(unrelated_listener, harness, mode):
    unrelated, port = unrelated_listener
    harness.mode = mode
    with pytest.raises(RuntimeError, match="offline body failure"):
        with d.forward("review-frontend", 8080, free_port()):
            raise RuntimeError("offline body failure")
    assert harness.processes[0].poll() is not None
    assert harness.processes[0].stdout.closed
    assert unrelated.poll() is None
    exchange(port)
