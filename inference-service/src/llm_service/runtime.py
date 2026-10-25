"""Single image entry point: own Ollama, health endpoint and Thrift lifecycle."""

import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm_service.config import Settings
from llm_service.errors import ExecutionError
from llm_service.handler import InferenceHandler
from llm_service.lifecycle import OllamaProcess
from llm_service.ollama import OllamaAdapter
from llm_service.server import RpcServer


def health_handler(model, handler):
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"/live", "/ready"}:
                self.send_error(404)
                return
            live = model.process.alive() or model.process.recovering.is_set()
            healthy = live if self.path == "/live" else model.ready() and not handler.closing
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Health


def serve(settings, process=None, model=None, stop=None):
    process = process or OllamaProcess(settings)
    model = model or OllamaAdapter(settings, process)
    stop = stop or threading.Event()
    handler = server = health = None
    threads = []
    started = []
    finished = threading.Event()

    def interrupt_startup():
        while not finished.wait(0.05):
            if stop.is_set():
                process.close()
                return

    watcher = threading.Thread(target=interrupt_startup, daemon=True)
    watcher.start()
    try:
        process.start()
        model.load()
        if stop.is_set() or process.closing.is_set():
            return
        handler = InferenceHandler(model, settings)
        server = RpcServer(
            (settings.thrift_bind_host, settings.thrift_port), handler, settings.rpc_connections
        )
        health = ThreadingHTTPServer(
            (settings.health_bind_host, settings.health_port), health_handler(model, handler)
        )
        for endpoint in (server, health):
            thread = threading.Thread(
                target=endpoint.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
            )
            thread.start()
            threads.append(thread)
            started.append(endpoint)
        logging.info("inference_ready")
        while not stop.wait(0.1):
            if (
                handler.closing
                or process.closing.is_set()
                or (not process.alive() and not process.recovering.is_set())
            ):
                raise RuntimeError("Ollama child exited")
    finally:
        finished.set()
        if handler:
            handler.begin_close()
        process.close()
        try:
            if handler:
                handler.close(settings.shutdown_seconds)
        finally:
            for endpoint in (server, health):
                if endpoint:
                    if endpoint in started:
                        endpoint.shutdown()
                    endpoint.server_close()
            for thread in threads:
                thread.join(2)
            watcher.join(3)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        serve(Settings(), stop=stop)
    except ExecutionError as error:
        if stop.is_set():
            return
        logging.error("inference_failed code=%s", error.code)
        raise SystemExit(1) from None
    except Exception:
        if stop.is_set():
            return
        logging.error("inference_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
