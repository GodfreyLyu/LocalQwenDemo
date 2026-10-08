"""Offline Kubernetes conditional-delete protocol."""

import json

import pytest


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.loopback
def test_kubectl_raw_delete_transmits_uid_and_version_to_offline_server(tmp_path):
    """Exercise the real CLI transport against a disposable loopback HTTP fixture only."""
    import os
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_DELETE(self):
            assert "Authorization" not in self.headers
            assert "Cookie" not in self.headers
            if self.headers.get("Transfer-Encoding") == "chunked":
                body = b""
                while size := int(self.rfile.readline().strip(), 16):
                    body += self.rfile.read(size)
                    assert self.rfile.read(2) == b"\r\n"
                assert self.rfile.readline() == b"\r\n"
            else:
                body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path.split("?", 1)[0], json.loads(body)))
            body = b'{"apiVersion":"v1","kind":"Status","status":"Success"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = tmp_path / "offline-kubeconfig"
        config.write_text(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Config",
                    "clusters": [
                        {
                            "name": "offline",
                            "cluster": {"server": f"http://127.0.0.1:{server.server_port}"},
                        }
                    ],
                    "users": [{"name": "offline", "user": {}}],
                    "contexts": [
                        {"name": "offline", "context": {"cluster": "offline", "user": "offline"}}
                    ],
                    "current-context": "offline",
                }
            )
        )
        config.chmod(0o600)
        path = "/api/v1/namespaces/local-review-demo/services/offline-fixture"
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": "fixture-uid", "resourceVersion": "42"},
            "propagationPolicy": "Foreground",
        }
        try:
            result = subprocess.run(
                [
                    "kubectl",
                    "--kubeconfig",
                    str(config),
                    "--context",
                    "offline",
                    "--namespace",
                    "local-review-demo",
                    "--request-timeout=5s",
                    "delete",
                    "--raw",
                    path,
                    "-f",
                    "-",
                ],
                input=json.dumps(options),
                text=True,
                capture_output=True,
                timeout=10,
                env=os.environ | {"NO_PROXY": "127.0.0.1"},
            )
            assert result.returncode == 0, "Offline kubectl DELETE transport failed"
            assert received == [(path, options)]
        finally:
            server.shutdown()
            thread.join(timeout=3)
