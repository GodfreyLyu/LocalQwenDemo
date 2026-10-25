"""Exercise the detached launcher with real processes and a test-owned kubectl double."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from deployment import endpoints
from tooling_paths import ROOT

FAKE_KUBECTL = r"""#!/usr/bin/env python3
import json, os, signal, socket, sys
from pathlib import Path

args = sys.argv[1:]
root = Path(os.environ["ENDPOINT_TEST_ROOT"])
if "get" in args:
    if "configmap" in args:
        origin = "http://localhost:" + os.environ["ENDPOINT_TEST_FRONTEND"]
        print(json.dumps({"data": {"ALLOWED_ORIGIN": origin}}))
    else:
        items = []
        definitions = [
            ("local-review-demo", "review-frontend", 8080),
            ("local-review-demo", "review-backend", 8000),
            ("monitoring", "monitoring-grafana", 80),
            ("local-inference", "review-ollama", 9091),
            ("flink-lab", "flink-jobmanager", 8081),
            ("spark", "spark-connect-server", 4040),
            ("another-project", "review-frontend", 8080),
        ]
        for ns, name, port in definitions:
            metadata = {"namespace": ns, "name": name, "labels": {"app": name}}
            spec = {"selector": {"app": name}, "ports": [{"port": port}]}
            items.append({"kind": "Service", "metadata": metadata, "spec": spec})
            if ns != "local-inference":
                status = {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}
                items.append({"kind": "Pod", "metadata": metadata, "status": status})
        print(json.dumps({"items": items}))
    sys.exit(0)

name = next(a.split("/", 1)[1] for a in args if a.startswith("service/"))
if name == "review-backend" and os.environ.get("ENDPOINT_TEST_FAIL"):
    print("simulated forwarding failure", flush=True)
    sys.exit(1)
with (root / (name + ".pids")).open("a") as output:
    output.write(str(os.getpid()) + "\n")
port, remote = map(int, args[-1].split(":"))
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen()
print(f"Forwarding from 127.0.0.1:{port} -> {3000 if remote == 80 else remote}", flush=True)
while True:
    client, _ = s.accept()
    with client:
        try:
            if client.recv(4096):
                client.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        except OSError:
            pass
"""


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def eventually(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    pytest.fail("Timed out waiting for launcher state")


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    fake = tmp_path / "kubectl"
    fake.write_text(FAKE_KUBECTL)
    fake.chmod(0o700)
    frontend_port = free_port()
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ENDPOINT_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("ENDPOINT_TEST_FRONTEND", str(frontend_port))
    # A short UNIX socket path also works when pytest's own tmp_path is long on macOS.
    with tempfile.TemporaryDirectory(prefix="endpoints-", dir="/tmp") as state:
        command = [
            sys.executable,
            str(ROOT / "scripts/minikube_endpoints.py"),
            "--context",
            "offline-test",
            "--state-dir",
            state,
            "--timeout",
            "3",
        ]

        def run(*args):
            return subprocess.run(
                [*command, *args], cwd=tmp_path, capture_output=True, text=True, timeout=20
            )

        yield run, Path(state), tmp_path, frontend_port
        result = run("stop")
        assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.loopback
@pytest.mark.recovery
def test_background_lifecycle_reconnect_and_port_ownership(launcher):
    run, state, root, port = launcher
    with socket.socket() as unrelated:
        unrelated.bind(("127.0.0.1", 0))
        unrelated.listen()
        occupied = unrelated.getsockname()[1]
        conflict = run("--port", f"backend={occupied}")
        assert conflict.returncode == 1
        assert "Cannot bind local port" in conflict.stderr
        assert not (state / "control.sock").exists()
        assert unrelated.getsockname()[1] == occupied

        started = run("--port", f"backend={free_port()}", "--port", f"grafana={free_port()}")
        assert started.returncode == 0, started.stderr + started.stdout
        assert f"http://localhost:{port}" in started.stdout
        assert "no ready running pod" in started.stdout
        assert "service not installed" in started.stdout
        snapshot = json.loads((state / "endpoints.json").read_text())
        active = [row for row in snapshot["rows"] if row["state"] == "READY"]
        assert len(active) == 3
        assert len(snapshot["rows"]) == 8
        assert {row["namespace"] for row in snapshot["rows"]} == {
            "local-review-demo",
            "local-inference",
            "argocd",
            "monitoring",
        }
        assert not (root / "flink-jobmanager.pids").exists()
        assert not (root / "spark-connect-server.pids").exists()
        # The caller has exited; every reported endpoint still carries traffic.
        for row in active:
            with socket.create_connection(("127.0.0.1", row["local"]), timeout=2) as client:
                client.sendall(b"GET / HTTP/1.0\r\n\r\n")
                assert b"200 OK" in client.recv(4096)

        repeated = run()
        assert repeated.returncode == 0, repeated.stderr
        assert "Already running" in repeated.stdout
        assert json.loads((state / "endpoints.json").read_text())["pid"] == snapshot["pid"]
        pids = root / "review-backend.pids"
        first_pid = int(pids.read_text().strip())
        os.kill(first_pid, signal.SIGTERM)
        eventually(lambda: len(pids.read_text().splitlines()) == 2)

        def recovered():
            result = run("status")
            return result.returncode == 0 and "RETRYING" not in result.stdout

        eventually(recovered)
        assert (
            json.loads((state / "endpoints.json").read_text())["rows"][1]["local"]
            == active[1]["local"]
        )
        assert run("stop").returncode == 0
        assert run("status").returncode == 1
        for row in active:
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", row["local"]), timeout=0.2)
        # The unrelated listener survives both startup failure and managed shutdown.
        with socket.create_connection(("127.0.0.1", occupied), timeout=1):
            client, _ = unrelated.accept()
            client.close()


@pytest.mark.integration
@pytest.mark.loopback
def test_frontend_port_preserves_browser_origin(launcher):
    run, state, _, port = launcher
    result = run("--port", f"frontend={port + 1 if port < 65535 else port - 1}")
    assert result.returncode == 1
    assert "must match deployed ALLOWED_ORIGIN" in result.stderr
    assert not (state / "control.sock").exists()


@pytest.mark.integration
@pytest.mark.loopback
def test_partial_start_reports_failure_and_keeps_healthy_endpoints(launcher, monkeypatch):
    run, state, _, _ = launcher
    monkeypatch.setenv("ENDPOINT_TEST_FAIL", "1")
    result = run()
    assert result.returncode == 1
    assert "RETRYING" in result.stdout
    snapshot = json.loads((state / "endpoints.json").read_text())
    rows = {row["key"]: row for row in snapshot["rows"]}
    assert rows["frontend"]["state"] == "READY"
    assert rows["backend"]["state"] == "RETRYING"
    assert "simulated forwarding failure" in (state / "backend.log").read_text()


@pytest.mark.integration
@pytest.mark.loopback
def test_occupied_preferred_port_uses_available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied = listener.getsockname()[1]
        chosen = endpoints.choose_port(occupied, fixed=False)
        assert chosen != occupied
        with socket.socket() as other:
            other.bind(("127.0.0.1", chosen))


@pytest.mark.unit
def test_control_socket_permission_error_is_not_reported_as_stopped(monkeypatch, tmp_path):
    def denied(*_):
        raise PermissionError("permission denied")

    monkeypatch.setattr(endpoints, "request", denied)
    with pytest.raises(endpoints.EndpointError, match="Cannot inspect background supervisor"):
        endpoints.current(tmp_path)
