"""Background loopback endpoints for LocalQwenDemo's app and platform; stdlib only."""

import argparse
import errno
import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from tooling_paths import ROOT


@dataclass(frozen=True)
class Endpoint:
    key: str
    label: str
    namespace: str
    service: str
    remote: int
    local: int
    scheme: str = "http"


# Explicit repository scope: deploy/helm/{local-review,local-ollama} and
# infra/local-platform/terraform/platform/{argocd,monitoring}.tf. Never forward
# unrelated services merely because they are installed in the same cluster.
ENDPOINTS = (
    Endpoint("frontend", "Review app", "local-review-demo", "review-frontend", 8080, 8080),
    Endpoint("backend", "Review API", "local-review-demo", "review-backend", 8000, 8000),
    Endpoint("dynamodb", "DynamoDB API", "local-review-demo", "review-dynamodb", 8000, 8001),
    Endpoint("inference", "LLM Thrift", "local-inference", "review-ollama", 9091, 9091, "thrift"),
    Endpoint("argocd", "Argo CD", "argocd", "argocd-server", 443, 8443, "https"),
    Endpoint("grafana", "Grafana", "monitoring", "monitoring-grafana", 80, 3000),
    Endpoint(
        "prometheus",
        "Prometheus",
        "monitoring",
        "monitoring-kube-prometheus-prometheus",
        9090,
        9090,
    ),
    Endpoint(
        "alertmanager",
        "Alertmanager",
        "monitoring",
        "monitoring-kube-prometheus-alertmanager",
        9093,
        9093,
    ),
)


class EndpointError(Exception):
    pass


def kubectl_json(kubectl, context, *args):
    result = subprocess.run(
        [kubectl, "--context", context, "--request-timeout=10s", *args, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise EndpointError(result.stderr.strip() or "kubectl failed")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def choose_port(preferred, *, fixed):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", preferred))
            listener.listen()
            return preferred
        except OSError as exc:
            if fixed or exc.errno != errno.EADDRINUSE:
                raise EndpointError(
                    f"Cannot bind local port {preferred}: {exc}. "
                    "Stop its current listener or choose a different --port KEY=PORT."
                ) from None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def discover(args):
    kubectl = shutil.which("kubectl")
    if not kubectl:
        raise EndpointError("kubectl is required on PATH")
    inventory = kubectl_json(kubectl, args.context, "get", "services,pods", "-A")["items"]
    services = {
        (item["metadata"]["namespace"], item["metadata"]["name"]): item
        for item in inventory
        if item["kind"] == "Service"
    }
    pods = [item for item in inventory if item["kind"] == "Pod"]
    overrides = {}
    for value in args.port:
        try:
            key, number = value.split("=", 1)
            port = int(number)
            if key not in {item.key for item in ENDPOINTS} or not 1024 <= port <= 65535:
                raise ValueError
            if key in overrides:
                raise ValueError
        except ValueError:
            raise EndpointError(
                f"Invalid --port {value!r}; use a unique endpoint KEY=1024..65535"
            ) from None
        overrides[key] = port

    frontend_origin = "http://localhost:8080"
    if ("local-review-demo", "review-frontend") in services:
        config = kubectl_json(
            kubectl,
            args.context,
            "-n",
            "local-review-demo",
            "get",
            "configmap",
            "review-config",
            "--ignore-not-found",
        )
        frontend_origin = config.get("data", {}).get("ALLOWED_ORIGIN", frontend_origin)
        origin = urlsplit(frontend_origin)
        if origin.scheme != "http" or origin.hostname not in {"localhost", "127.0.0.1"}:
            raise EndpointError(f"Frontend ALLOWED_ORIGIN must be local HTTP: {frontend_origin}")
        origin_port = origin.port or 80
        if "frontend" in overrides and overrides["frontend"] != origin_port:
            raise EndpointError(
                f"Frontend port must match deployed ALLOWED_ORIGIN={frontend_origin}"
            )
        overrides["frontend"] = origin_port

    rows, used = [], set()
    for endpoint in ENDPOINTS:
        endpoint = replace(endpoint, local=overrides.get(endpoint.key, endpoint.local))
        row = {**asdict(endpoint), "state": "STARTING", "detail": "", "url": ""}
        service = services.get((endpoint.namespace, endpoint.service))
        if not service:
            row.update(state="SKIPPED", detail="service not installed")
        elif not any(
            port["port"] == endpoint.remote and port.get("protocol", "TCP") == "TCP"
            for port in service["spec"].get("ports", [])
        ):
            row.update(state="ERROR", detail=f"service has no TCP port {endpoint.remote}")
        elif not service["spec"].get("selector"):
            row.update(state="SKIPPED", detail="service has no pod selector")
        elif not any(
            pod["metadata"]["namespace"] == endpoint.namespace
            and all(
                pod["metadata"].get("labels", {}).get(k) == v
                for k, v in service["spec"]["selector"].items()
            )
            and pod.get("status", {}).get("phase") == "Running"
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in pod.get("status", {}).get("conditions", [])
            )
            and not pod["metadata"].get("deletionTimestamp")
            for pod in pods
        ):
            row.update(state="SKIPPED", detail="no ready running pod (stopped/completed/pending)")
        else:
            fixed = endpoint.key == "frontend" or endpoint.key in overrides
            local = choose_port(endpoint.local, fixed=fixed)
            while local in used:
                if fixed:
                    raise EndpointError(f"Duplicate local port {local}; adjust --port overrides")
                local = choose_port(0, fixed=False)
            used.add(local)
            host = "localhost" if endpoint.key == "frontend" else "127.0.0.1"
            row.update(local=local, url=f"{endpoint.scheme}://{host}:{local}")
            if endpoint.key == "frontend":
                row["url"] = frontend_origin
        rows.append(row)
    return {"context": args.context, "kubectl": kubectl, "timeout": args.timeout, "rows": rows}


def request(state_dir, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(state_dir / "control.sock"))
        client.sendall((command + "\n").encode())
        chunks = []
        while chunk := client.recv(65536):
            chunks.append(chunk)
        return json.loads(b"".join(chunks))


def current(state_dir):
    try:
        return request(state_dir, "status")
    except (FileNotFoundError, ConnectionRefusedError):
        return None
    except (OSError, ValueError) as exc:
        raise EndpointError(f"Cannot inspect background supervisor: {exc}") from None


def unlocked(path):
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


def stop_child(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


class Supervisor:
    def __init__(self, plan, state_dir):
        self.plan, self.state_dir = plan, state_dir
        self.stopping = threading.Event()
        self.mutex = threading.Lock()
        self.initial = {row["key"]: threading.Event() for row in plan["rows"]}
        self.threads = []

    def update(self, row, state, detail=""):
        with self.mutex:
            row.update(state=state, detail=detail)

    def snapshot(self):
        with self.mutex:
            return {
                "context": self.plan["context"],
                "pid": os.getpid(),
                "initial_complete": all(event.is_set() for event in self.initial.values()),
                "rows": [dict(row) for row in self.plan["rows"]],
            }

    def worker(self, row):
        """Keep only our own child alive, with bounded startup and interruptible retries."""
        log_path = self.state_dir / f"{row['key']}.log"
        command = [
            self.plan["kubectl"],
            "--context",
            self.plan["context"],
            "-n",
            row["namespace"],
            "port-forward",
            "--address=127.0.0.1",
            "--pod-running-timeout=10s",
            "service/" + row["service"],
            f"{row['local']}:{row['remote']}",
        ]
        while not self.stopping.is_set():
            proc = None
            try:
                self.update(row, "STARTING")
                with log_path.open("ab", buffering=0) as output, log_path.open("rb") as reader:
                    reader.seek(0, os.SEEK_END)
                    proc = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                    )
                    deadline = time.monotonic() + self.plan["timeout"]
                    pending = b""
                    announcement = f"Forwarding from 127.0.0.1:{row['local']} -> ".encode()
                    ready = False
                    while not self.stopping.is_set() and proc.poll() is None:
                        if not ready:
                            pending = (pending + reader.read(8192))[-16384:]
                            if announcement in pending:
                                with socket.create_connection(
                                    ("127.0.0.1", row["local"]), timeout=1
                                ):
                                    pass
                                if proc.poll() is None:
                                    ready = True
                                    self.update(row, "READY")
                                    self.initial[row["key"]].set()
                            if not ready and time.monotonic() >= deadline:
                                raise EndpointError("startup timed out")
                        self.stopping.wait(0.15 if not ready else 0.5)
                    if not self.stopping.is_set():
                        raise EndpointError(f"kubectl exited ({proc.returncode})")
            except (OSError, EndpointError, subprocess.SubprocessError) as exc:
                self.update(row, "RETRYING", f"{exc}; see {log_path.name}")
            finally:
                if proc is not None:
                    stop_child(proc)
                self.initial[row["key"]].set()
            self.stopping.wait(2)

    def serve(self):
        with (self.state_dir / "daemon.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._serve()

    def _serve(self):
        control_path = self.state_dir / "control.sock"
        control_path.unlink(missing_ok=True)
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(number, lambda *_: self.stopping.set())
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(control_path))
                server.listen(8)
                server.settimeout(0.25)
                for row in self.plan["rows"]:
                    if row["state"] != "STARTING":
                        self.initial[row["key"]].set()
                        continue
                    thread = threading.Thread(target=self.worker, args=(row,))
                    self.threads.append(thread)
                    thread.start()
                while not self.stopping.is_set():
                    try:
                        connection, _ = server.accept()
                    except TimeoutError:
                        continue
                    with connection:
                        connection.settimeout(2)
                        try:
                            command = connection.recv(64).decode().strip()
                            snapshot = self.snapshot()
                            (self.state_dir / "endpoints.json").write_text(
                                json.dumps(snapshot, indent=2) + "\n"
                            )
                            connection.sendall(json.dumps(snapshot).encode())
                            if command == "stop":
                                self.stopping.set()
                        except (OSError, UnicodeError):
                            continue
        finally:
            self.stopping.set()
            for thread in self.threads:
                thread.join()
            control_path.unlink(missing_ok=True)


def display(snapshot, state_dir):
    print(f"Minikube endpoints (context: {snapshot['context']}, supervisor PID: {snapshot['pid']})")
    headers = ("SERVICE", "ENDPOINT", "KUBERNETES SERVICE", "STATE")
    rows = [headers]
    for row in snapshot["rows"]:
        rows.append(
            (
                row["label"],
                row["url"] if row["state"] == "READY" else "-",
                f"{row['namespace']}/{row['service']}:{row['remote']}",
                row["state"],
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    for row in rows:
        print(
            "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)).rstrip()
        )
    for row in snapshot["rows"]:
        if row["detail"]:
            print(f"  {row['key']}: {row['detail']}")
    print(f"Logs and latest endpoint snapshot: {state_dir}")
    print("Argo CD uses its cluster certificate; your browser may ask you to accept it.")
    return (
        0
        if any(r["state"] == "READY" for r in snapshot["rows"])
        and all(r["state"] in {"READY", "SKIPPED"} for r in snapshot["rows"])
        else 1
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("start", "status", "stop"), default="start")
    parser.add_argument(
        "--context", default="minikube", help="explicit kubeconfig context (default: minikube)"
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="private logs/control directory; default: .local/endpoints/CONTEXT",
    )
    parser.add_argument(
        "--port",
        action="append",
        default=[],
        metavar="KEY=PORT",
        help="pin a local port; repeat as needed (keys: "
        + ", ".join(e.key for e in ENDPOINTS)
        + ")",
    )
    parser.add_argument(
        "--timeout", type=float, default=20, help="per-forward startup timeout in seconds"
    )
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout must be between 0 and 300 seconds")
    state_dir = (
        args.state_dir or ROOT / ".local" / "endpoints" / re.sub(r"[^\w.-]", "_", args.context)
    ).resolve()
    os.umask(0o077)
    state_dir.mkdir(parents=True, exist_ok=True)
    if args.serve:
        Supervisor(json.loads((state_dir / "plan.json").read_text()), state_dir).serve()
        return 0
    with (state_dir / "command.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        snapshot = current(state_dir)
        if snapshot and snapshot["context"] != args.context:
            raise EndpointError("State directory belongs to another context; choose --state-dir")
        if args.command == "stop":
            if snapshot:
                request(state_dir, "stop")
            deadline = time.monotonic() + 12
            while not unlocked(state_dir / "daemon.lock"):
                if time.monotonic() > deadline:
                    raise EndpointError(
                        "Supervisor cleanup is still running; inspect supervisor.log"
                    )
                time.sleep(0.1)
            print(
                "Background endpoint forwards stopped."
                if snapshot
                else "No background endpoint forwards running."
            )
            return 0
        if args.command == "status":
            if not snapshot:
                print("No background endpoint forwards running.")
                return 1
            return display(snapshot, state_dir)
        if snapshot:
            print(
                "Already running; existing forwards retained. "
                "Use stop then start to change ports or rediscover pods."
            )
            return display(snapshot, state_dir)
        if not unlocked(state_dir / "daemon.lock"):
            raise EndpointError("Supervisor is starting or stopping; retry shortly")
        plan = discover(args)
        if not any(row["state"] == "STARTING" for row in plan["rows"]):
            raise EndpointError(
                "No configured endpoints have ready pods; inspect kubectl get pods -A"
            )
        (state_dir / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        with (state_dir / "supervisor.log").open("ab") as output:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "minikube_endpoints.py"),
                    "--serve",
                    "--state-dir",
                    str(state_dir),
                    "--context",
                    args.context,
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        print(f"Starting background forwards for context {args.context}...", flush=True)
        deadline = time.monotonic() + args.timeout + 10
        while time.monotonic() < deadline:
            snapshot = current(state_dir)
            if snapshot and snapshot["initial_complete"]:
                return display(snapshot, state_dir)
            if proc.poll() is not None:
                raise EndpointError(f"Supervisor exited; see {state_dir / 'supervisor.log'}")
            time.sleep(0.15)
        raise EndpointError(
            f"Startup still pending; run status. See {state_dir / 'supervisor.log'}"
        )


def entry():
    try:
        raise SystemExit(main())
    except (EndpointError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Interrupted. Use status or stop to inspect background forwards.", file=sys.stderr)
        raise SystemExit(130) from None
