"""Ollama child ownership, adapted from deploy/images/ollama-vulkan/runtime.py."""

import os
import signal
import socket
import subprocess
import threading
from pathlib import Path


class OllamaProcess:
    def __init__(self, settings, *, command=("ollama", "serve")):
        self.settings, self.command = settings, command
        self.process = None
        self.lock = threading.RLock()
        self.closing = threading.Event()
        self.recovering = threading.Event()

    def start(self):
        with self.lock:
            if self.closing.is_set():
                raise RuntimeError("Shutdown requested")
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("Ollama is already running")
            # Fail before contacting an unrelated host daemon on the selected port.
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", self.settings.ollama_port))
            environment = os.environ.copy()
            environment.update(
                OLLAMA_HOST=f"127.0.0.1:{self.settings.ollama_port}",
                OLLAMA_NUM_PARALLEL="1",
                OLLAMA_MAX_LOADED_MODELS="1",
                OLLAMA_MAX_QUEUE="1",
                OLLAMA_KEEP_ALIVE="5m",
                OLLAMA_NO_CLOUD="1",
            )
            for name, default in (
                ("HOME", "/tmp/ollama-home"),
                ("XDG_CACHE_HOME", "/tmp/cache"),
                ("XDG_RUNTIME_DIR", "/tmp/runtime"),
            ):
                directory = environment.setdefault(name, default)
                Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
            # Suppress native logs: Ollama errors/debug output may include user content.
            self.process = subprocess.Popen(
                self.command,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    def alive(self):
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def abort(self):
        """Kill the entire process group, then reap the owner before confirming cancellation."""
        with self.lock:
            self.recovering.set()
            if self.process is None:
                return
            process = self.process
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            # The server may exit before its runner. Always kill the remaining group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def restart(self):
        self.abort()
        self.start()

    def close(self):
        self.closing.set()
        self.abort()
