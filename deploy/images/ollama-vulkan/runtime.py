"""Supervise Ollama and admit traffic only after real, pinned-model GPU inference."""

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


@dataclass(frozen=True)
class Config:
    model: str
    digest: str
    context: int
    threads: int
    pull: bool
    wait_seconds: int

    @classmethod
    def from_env(cls):
        return cls(
            os.environ["MODEL_NAME"],
            os.environ["MODEL_DIGEST"].removeprefix("sha256:"),
            int(os.environ.get("MODEL_CONTEXT", "4096")),
            int(os.environ.get("MODEL_THREADS", "2")),
            os.environ.get("MODEL_PULL_IF_MISSING", "true") == "true",
            int(os.environ.get("MODEL_WAIT_SECONDS", "1200")),
        )


class ModelNotLoaded(RuntimeError):
    pass


class Client:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, payload=None, timeout=5):
        request = urllib.request.Request(
            self.url + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError("Ollama returned an invalid response")
        return result


def installed(client, config):
    models = client.call("/api/tags").get("models", [])
    matches = [m for m in models if m.get("name") == config.model]
    if not matches:
        return False
    if len(matches) != 1 or matches[0].get("digest", "").removeprefix("sha256:") != config.digest:
        raise RuntimeError("Installed model digest differs from the pinned digest")
    return True


def gpu_state(client, config):
    models = client.call("/api/ps").get("models", [])
    matches = [m for m in models if m.get("name") == config.model]
    if not matches:
        raise ModelNotLoaded("Pinned model is not resident")
    model = matches[0]
    if len(matches) != 1 or model.get("digest", "").removeprefix("sha256:") != config.digest:
        raise RuntimeError("Resident model digest differs from the pinned digest")
    size, vram = model.get("size"), model.get("size_vram")
    if type(size) is not int or type(vram) is not int or size <= 0 or vram < size:
        raise RuntimeError("Full GPU loading required; CPU or partial offload is not accepted")
    return {"model": config.model, "digest": config.digest, "size": size, "size_vram": vram}


def warmup(client, config):
    if not installed(client, config):
        raise ModelNotLoaded("Pinned model is not installed")
    result = client.call(
        "/api/chat",
        {
            "model": config.model,
            "messages": [{"role": "user", "content": "Reply with the word Ready."}],
            "think": False,
            "stream": False,
            "keep_alive": -1,
            "options": {
                "num_ctx": config.context,
                "num_predict": 8,
                "num_gpu": 999,
                "num_thread": config.threads,
                "temperature": 0,
            },
        },
        timeout=180,
    )
    if (
        result.get("model") != config.model
        or result.get("done") is not True
        or not result.get("message", {}).get("content", "").strip()
        or result.get("eval_count", 0) <= 0
    ):
        raise RuntimeError("GPU warmup did not produce a completed, nonempty response")
    state = gpu_state(client, config)
    print(json.dumps({"event": "gpu_verified", **state}), flush=True)
    return state


def prepare(client, config, stop):
    deadline = time.monotonic() + config.wait_seconds
    while time.monotonic() < deadline and not stop.is_set():
        try:
            client.call("/api/version")
            break
        except (OSError, ValueError):
            stop.wait(1)
    else:
        raise RuntimeError("Ollama API did not start")
    while not stop.is_set():
        if installed(client, config):
            return warmup(client, config)
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for the model in the PVC")
        if config.pull:
            print(json.dumps({"event": "model_pull", "model": config.model}), flush=True)
            client.call(
                "/api/pull",
                {"model": config.model, "stream": False},
                timeout=max(1, deadline - time.monotonic()),
            )
            if not installed(client, config):
                raise RuntimeError("Model pull completed without the pinned model")
            return warmup(client, config)
        stop.wait(2)
    raise RuntimeError("Shutdown requested")


def handler(client, config, process, verified):
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/live", "/ready"):
                self.send_error(404)
                return
            try:
                if process.poll() is not None:
                    raise RuntimeError("Ollama process exited")
                if self.path == "/ready":
                    if not verified.is_set():
                        raise RuntimeError("Model GPU validation is pending")
                    result = gpu_state(client, config)
                else:
                    result = client.call("/api/version")
                status = 200
            except (OSError, ValueError, RuntimeError) as error:
                status, result = 503, {"error": str(error)}
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Health


def serve(config):
    for name in ("HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        Path(os.environ[name]).mkdir(parents=True, exist_ok=True, mode=0o700)
    stop, verified = threading.Event(), threading.Event()
    client = Client("http://127.0.0.1:11434")
    process = subprocess.Popen(["ollama", "serve"], stdin=subprocess.DEVNULL)

    def shutdown(*args):
        stop.set()
        verified.clear()
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server = ThreadingHTTPServer(("0.0.0.0", 11435), handler(client, config, process, verified))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        prepare(client, config, stop)
        verified.set()
        while not stop.wait(15):
            if process.poll() is not None:
                raise RuntimeError("Ollama process exited")
            try:
                gpu_state(client, config)
            except ModelNotLoaded:
                # Clients may override keep_alive; restore residency after an idle unload.
                verified.clear()
                warmup(client, config)
                verified.set()
    finally:
        shutdown()
        server.shutdown()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "verify"))
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    config = Config.from_env()
    if args.command == "verify":
        warmup(Client(args.url), config)
    else:
        serve(config)
