"""Real child processes exercise ownership, cancellation, crash detection and cleanup."""

import os
import signal
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from llm_service.config import Settings
from llm_service.lifecycle import OllamaProcess
from llm_service.runtime import serve


def child(tmp_path, monkeypatch, script="import time; time.sleep(60)"):
    for name in ("HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        native_port = probe.getsockname()[1]
    return OllamaProcess(
        Settings(_env_file=None, thrift_port=0, health_port=0, ollama_port=native_port),
        command=(sys.executable, "-c", script),
    )


def test_start_and_abort_reap_real_child_and_force_loopback(tmp_path, monkeypatch):
    output = tmp_path / "environment.json"
    script = (
        "import json,os,time; "
        f"open({str(output)!r},'w').write("
        "json.dumps({'OLLAMA_HOST':os.environ['OLLAMA_HOST']})); "
        "time.sleep(60)"
    )
    process = child(tmp_path, monkeypatch, script)
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:9999")
    process.start()
    try:
        for _ in range(100):
            if output.exists():
                break
            time.sleep(0.01)
        import json

        assert (
            json.loads(output.read_text())["OLLAMA_HOST"]
            == f"127.0.0.1:{process.settings.ollama_port}"
        )
        assert process.alive()
        with pytest.raises(RuntimeError):
            process.start()
    finally:
        process.close()
    assert process.process.poll() is not None
    assert not process.alive()


def test_group_cleanup_kills_native_runner_even_if_owner_exits(tmp_path, monkeypatch):
    runner_pid = tmp_path / "runner.pid"
    script = (
        "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
        f"open({str(runner_pid)!r},'w').write(str(p.pid)); time.sleep(60)"
    )
    process = child(tmp_path, monkeypatch, script)
    process.start()
    try:
        for _ in range(100):
            if runner_pid.exists():
                break
            time.sleep(0.01)
        pid = int(runner_pid.read_text())
        process.abort()
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail("Native runner survived process group cleanup")
    finally:
        process.close()


def test_startup_failure_always_cleans_up_child(tmp_path, monkeypatch):
    process = child(tmp_path, monkeypatch)

    def fail_load():
        raise RuntimeError("startup failure")

    with pytest.raises(RuntimeError, match="startup failure"):
        serve(process.settings, process=process, model=SimpleNamespace(load=fail_load))
    assert process.process.poll() is not None


def test_child_exit_terminates_service_and_closes_listeners(tmp_path, monkeypatch):
    process = child(tmp_path, monkeypatch)
    loaded = threading.Event()
    model = SimpleNamespace(load=loaded.set, ready=process.alive, quantization=None, device="gpu")
    errors = []

    def run():
        try:
            serve(process.settings, process=process, model=model)
        except RuntimeError as error:
            errors.append(str(error))

    worker = threading.Thread(target=run)
    worker.start()
    assert loaded.wait(2)
    os.kill(process.process.pid, signal.SIGKILL)
    worker.join(3)
    assert not worker.is_alive()
    assert errors == ["Ollama child exited"]
    assert process.process.poll() is not None


def test_occupied_native_port_fails_without_starting_or_contacting_another_server(
    tmp_path, monkeypatch
):
    process = child(tmp_path, monkeypatch)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", process.settings.ollama_port))
        occupied.listen()
        with pytest.raises(OSError):
            process.start()
    assert process.process is None


def test_shutdown_interrupts_startup_and_reaps_the_native_child(tmp_path, monkeypatch):
    process = child(tmp_path, monkeypatch)
    stop, entered = threading.Event(), threading.Event()

    def loading():
        entered.set()
        assert process.closing.wait(2)

    model = SimpleNamespace(load=loading, ready=lambda: False, quantization=None, device=None)
    failures = []

    def run():
        try:
            serve(process.settings, process=process, model=model, stop=stop)
        except Exception as error:
            failures.append(type(error).__name__)

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(1)
    stop.set()
    worker.join(3)
    assert not worker.is_alive() and process.process.poll() is not None
    assert failures == []
