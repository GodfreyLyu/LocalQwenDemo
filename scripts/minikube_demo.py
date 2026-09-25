"""Deploy this application into a user-started local minikube; never manage cluster lifecycle.

The CLI selects a verified target, locks its state, then dispatches diagnostics,
deployment or acceptance. Target discovery, state contracts and acceptance live
in sibling modules. Writes require matching target/resource ownership; reports
separate readiness from inference and browser acceptance.
"""

import argparse
import contextlib
import errno
import fcntl
import json
import math
import os
import re
import secrets
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = Path(
    os.environ.get("LOCAL_QWEN_STATE_HOME")
    or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "local-qwen-demo"
)
STATE = STATE_ROOT
NAMESPACE = "local-review-demo"
PROFILE = ""
MINIKUBE_HOME = Path(os.environ.get("MINIKUBE_HOME", str(Path.home() / ".minikube")))
KUBECONFIG_FILE = STATE / "kubeconfig"
TARGET = {}
OWNER_KEY = "local-review-demo/owner"
LABEL = "app.kubernetes.io/part-of"
GIB = 1024**3
DYNAMODB = "amazon/dynamodb-local:3.1.0"
FORWARD_START_TIMEOUT = 10


class DemoError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise DemoError(message)


def clean_env():
    """Keep host AWS/HF credentials out of local subprocesses; use only the selected kubeconfig."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "HF_", "MINIKUBE_"))}
    env.update(
        AWS_CONFIG_FILE="/dev/null",
        AWS_SHARED_CREDENTIALS_FILE="/dev/null",
        AWS_EC2_METADATA_DISABLED="true",
        KUBECONFIG=str(KUBECONFIG_FILE),
        MINIKUBE_HOME=str(MINIKUBE_HOME),
    )
    return env


def run(args, *, data=None, check=True, timeout=120, env=None):
    # Capture errors: never echo arbitrary subprocess output, manifests or credentials.
    """Run one bounded subprocess from the checkout; withhold raw output on failure.

    No retries are implied: callers choose stage-specific deadlines and must not
    retry mutations without rechecking ownership and state."""
    try:
        result = subprocess.run(
            [str(a) for a in args],
            input=data,
            text=True,
            capture_output=True,
            check=False,
            env=env or clean_env(),
            timeout=timeout,
            cwd=ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DemoError(f"{args[0]} unavailable or timed out ({type(exc).__name__}).") from None
    if check and result.returncode:
        raise DemoError(
            f"{args[0]} {args[1]} failed (exit {result.returncode}). "
            "Use status/logs and the troubleshooting guide; raw output is withheld."
        )
    return result


def mk(*args, **kw):
    # A hard allowlist, in addition to tests, prevents future accidental lifecycle calls.
    require(tuple(args[:2]) == ("image", "load"), "Only minikube image load is allowed here.")
    require(PROFILE, "No selected running profile.")
    return run(["minikube", "--skip-audit", "-p", PROFILE, *args], **kw)


def kargs(*args, namespace=NAMESPACE):
    return [
        "kubectl",
        "--kubeconfig",
        str(KUBECONFIG_FILE),
        "--context",
        PROFILE,
        "--namespace",
        namespace,
        *args,
    ]


def k(*args, **kw):
    return run(kargs(*args), **kw)


def obj(kind, name, *, optional=False):
    if kind.lower() == "secret":
        # Ownership needs metadata only; never return Secret data to the deployment process.
        from minikube_undeploy import METADATA_PATH, parse_metadata

        value = k("get", kind, name, "--ignore-not-found", "-o", "jsonpath=" + METADATA_PATH).stdout
        require(optional or value.strip(), f"Missing {kind}/{name}.")
        return {"kind": "Secret", "metadata": parse_metadata(value)} if value.strip() else None
    value = k("get", kind, name, "--ignore-not-found", "-o", "json").stdout
    require(optional or value.strip(), f"Missing {kind}/{name}.")
    return json.loads(value) if value.strip() else None


def owned(resource, owner):
    require(
        resource["metadata"].get("annotations", {}).get(OWNER_KEY) == owner,
        f"Ownership conflict: {resource['kind']}/{resource['metadata']['name']}; not taking over.",
    )


def save(name, value):
    """Atomically replace a private target record. The caller must hold the operation lock."""
    path = STATE / name
    # Atomic replacement; umask and directory mode keep credentials private.
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temp.replace(path)


def state(optional=False):
    path = STATE / "owner.json"
    if optional and not path.exists():
        return None
    require(path.is_file(), "No application deployment state exists for this target; run up first.")
    from minikube_state import ownership, read

    value = read("owner.json")
    require(isinstance(value, dict), "Invalid ownership state; run up without deleting owner.json.")
    for key in ("profile", "minikube_home", "cluster_uid"):
        require(
            value.get(key) == TARGET.get(key),
            "Deployment state belongs to another minikube home/profile/cluster; no takeover.",
        )
    ownership(value)
    return value


def check_ownership(owner):
    """Refuse adoption of an existing namespace or any expected resource with another owner."""
    ns = obj("namespace", NAMESPACE, optional=True)
    if ns:
        require(
            owner is not None,
            "Ownership conflict: existing namespace has no matching target state. "
            "Use import-state --profile NAME --from-state /path/to/old/checkout; "
            "never delete owner.json or adopt resources.",
        )
        owned(ns, owner["owner"])
        if owner.get("namespace_uid"):
            require(ns["metadata"]["uid"] == owner["namespace_uid"], "Namespace identity changed.")
        for resource in render((owner or {}).get("port", 8080)) + [
            {"kind": "Secret", "metadata": {"name": "review-secrets"}}
        ]:
            existing = obj(resource["kind"], resource["metadata"]["name"], optional=True)
            if existing:
                owned(existing, owner["owner"])
    elif owner:
        require(not owner.get("namespace_uid"), "Owned namespace vanished; refusing recreation.")
    return ns


def guard_target():
    require_local_docker()
    uid = k("get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}").stdout
    require(uid == TARGET["cluster_uid"], "Target cluster identity changed during this operation.")


def guard_cluster():
    guard_target()
    owner = state()
    require(check_ownership(owner), "Application namespace is missing.")
    return owner


def native_arch(value):
    return {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(value)


def host_memory():
    if sys.platform == "darwin":
        total = int(run(["sysctl", "-n", "hw.memsize"]).stdout)
        vm = run(["vm_stat"]).stdout
        size = int(re.search(r"page size of (\d+) bytes", vm)[1])
        pages = sum(
            int(re.search(rf"{name}:\s+(\d+)", vm)[1])
            for name in ("Pages free", "Pages inactive", "Pages speculative")
        )
        return total, pages * size
    if sys.platform == "linux":
        fields = dict(re.findall(r"^(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M))
        return int(fields["MemTotal"]) * 1024, int(fields["MemAvailable"]) * 1024
    raise DemoError("Only macOS/Linux Docker-driver hosts are supported.")


def safe_errno(exc):
    value = exc.errno
    return f"errno={value} ({errno.errorcode.get(value, 'UNKNOWN')})"


def port_available(port):
    """Probe reusable TCP listener semantics; success does not reserve the port."""
    try:
        with socket.socket() as sock:
            # Permit rebinding after our earlier listener's accepted connections close.
            # listen() also rejects an active listener on platforms allowing a shared bind.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
        return True
    except OSError as exc:
        reason = {
            errno.EADDRINUSE: "address_in_use",
            errno.EACCES: "permission_denied",
            errno.EPERM: "permission_denied",
        }.get(exc.errno, "bind_failed")
        raise DemoError(
            f"Loopback port {port}: {reason}; {safe_errno(exc)}. "
            "Check local listener state and execution permissions; "
            "no existing listener is reused or terminated."
        ) from None


def doctor(args):
    plan = deployment_plan(args)
    require(
        not plan["report"]["blockers"],
        "Diagnostics failed: " + "; ".join(plan["report"]["blockers"]),
    )
    return plan


def deployment_plan(args):
    """Mandatory ownership/compatibility checks plus advisory resource diagnostics."""
    from minikube_target import preflight

    owner = state(optional=True)
    check_ownership(owner)
    from minikube_state import planned_options

    choices = planned_options(owner)
    plan = preflight(args, choices)
    plan["port"] = args.port or (choices or {}).get("port", 8080)
    return plan


def require_local_docker():
    # Never operate a user's remote Docker context.
    require(
        not os.environ.get("DOCKER_HOST") or os.environ["DOCKER_HOST"].startswith("unix://"),
        "Remote DOCKER_HOST is not permitted.",
    )
    context = json.loads(run(["docker", "context", "inspect"]).stdout)[0]
    require(
        context["Endpoints"]["docker"]["Host"].startswith("unix://"),
        "Docker context must use a local Unix socket.",
    )


def apply(resources, owner):
    for resource in resources:
        resource["metadata"].setdefault("annotations", {})[OWNER_KEY] = owner
        previous = obj(resource["kind"], resource["metadata"]["name"], optional=True)
        if previous:
            owned(previous, owner)
    payload = json.dumps({"apiVersion": "v1", "kind": "List", "items": resources})
    # No secret goes through apply: its last-applied annotation would duplicate secret material.
    k("apply", "-f", "-", data=payload)


def ensure_secret(owner):
    """Reuse a valid owned signing key or create the first key via stdin, never rotate it."""
    previous = obj("secret", "review-secrets", optional=True)
    if previous:
        owned(previous, owner)
        length = k(
            "get",
            "secret",
            "review-secrets",
            "-o",
            "go-template={{if .data.SIGNING_SECRET}}{{len (base64decode .data.SIGNING_SECRET)}}"
            "{{else}}0{{end}}",
        ).stdout.strip()
        valid = length.isdigit() and int(length) >= 32
        require(valid, "Existing review-secrets lacks a valid SIGNING_SECRET; refusing rotation.")
        return
    resource = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": "review-secrets",
            "namespace": NAMESPACE,
            "annotations": {OWNER_KEY: owner},
            "labels": {LABEL: NAMESPACE},
        },
        "stringData": {"SIGNING_SECRET": secrets.token_urlsafe(48)},
    }
    k("create", "-f", "-", data=json.dumps(resource))


def wait_rollout(name, seconds):
    print(f"Waiting for {name} (up to {seconds}s)…", flush=True)
    k("rollout", "status", "deployment/" + name, f"--timeout={seconds}s", timeout=seconds + 30)


@contextlib.contextmanager
def forward(service, remote_port, local_port=None, *, wait=False):
    """Own one loopback-forward child for this context and clean up only that process.

    A free-port probe is not a reservation. Require its exact announcement, live
    process and reachable socket within the deadline; never adopt a listener."""
    if local_port is None:
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                local_port = sock.getsockname()[1]
        except OSError as exc:
            raise DemoError(f"Loopback port allocation_failed; {safe_errno(exc)}.") from None
    port_available(local_port)
    try:
        proc = subprocess.Popen(
            kargs(
                "port-forward",
                "--address=127.0.0.1",
                "service/" + service,
                f"{local_port}:{remote_port}",
            ),
            env=clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        raise DemoError(f"Port-forward process_start_failed; {safe_errno(exc)}.") from None
    drainer = None
    stop_drainer = threading.Event()
    try:
        deadline = time.monotonic() + FORWARD_START_TIMEOUT
        pending = b""
        reason = "listener_not_confirmed"
        announced = False
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.1)[0]:
                chunk = os.read(proc.stdout.fileno(), 4096)
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if line == f"Forwarding from 127.0.0.1:{local_port} -> {remote_port}".encode():
                        announced = True
                    # Only fixed classifications escape this reader, never raw process output.
                    if b"address already in use" in line.lower():
                        reason = "address_in_use"
                    elif any(
                        p in line.lower()
                        for p in (b"permission denied", b"operation not permitted")
                    ):
                        reason = "permission_denied"
                pending = pending[-4096:]
            require(
                proc.poll() is None,
                f"Port-forward startup_failed on loopback port {local_port}: {reason}; "
                "the port may have changed after probing. No unrelated listener is reused.",
            )
            if announced:
                break
        require(announced, f"Port-forward startup_timeout on loopback port {local_port}: {reason}.")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                pass
        except OSError as exc:
            raise DemoError(f"Port-forward listener_unreachable; {safe_errno(exc)}.") from None
        require(proc.poll() is None, "Port-forward exited before listener readiness was confirmed.")

        def discard_output():
            while not stop_drainer.is_set():
                if select.select([proc.stdout], [], [], 0.1)[0]:
                    if not os.read(proc.stdout.fileno(), 4096):
                        break

        drainer = threading.Thread(target=discard_output, daemon=True)
        drainer.start()
        yield local_port
        if wait:
            while proc.poll() is None:
                time.sleep(0.2)
        require(
            proc.poll() is None, "Port-forward exited unexpectedly; forwarding is not verified."
        )
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise DemoError("Port-forward cleanup_timeout for the owned process.") from None
        finally:
            stop_drainer.set()
            if drainer:
                drainer.join(timeout=2)
            if proc.stdout:
                proc.stdout.close()


def init_users():
    # Preserve the existing initializer's loopback-only restriction unchanged.
    with forward("review-dynamodb", 8000) as port:
        env = clean_env() | {"DYNAMODB_ENDPOINT_URL": f"http://127.0.0.1:{port}"}
        run([sys.executable, ROOT / "scripts/init_local_users.py"], env=env)
        from app.local_dynamodb import local_client

        db = local_client(f"http://127.0.0.1:{port}")
        table = db.describe_table(TableName="llm-review-users")["Table"]
        require(
            table["TableStatus"] == "ACTIVE"
            and table["KeySchema"] == [{"AttributeName": "login_id", "KeyType": "HASH"}],
            "DynamoDB Local table schema/status mismatch.",
        )
    print("DynamoDB Local table verified; existing accounts retained.")


def render(port, images=None, cold_timeout=3600, storage_class="standard"):
    import yaml

    resources = list(
        yaml.safe_load_all(
            run(["kubectl", "kustomize", ROOT / "deploy/kustomize/overlays/minikube"]).stdout
        )
    )
    for resource in resources:
        if resource["kind"] == "PersistentVolumeClaim":
            resource["spec"]["storageClassName"] = storage_class
        if resource["kind"] == "ConfigMap" and resource["metadata"]["name"] == "review-config":
            resource["data"]["ALLOWED_ORIGIN"] = f"http://localhost:{port}"
        if resource["kind"] == "Deployment" and resource["metadata"]["name"] == "review-backend":
            resource["spec"]["progressDeadlineSeconds"] = cold_timeout + 120
            for container in resource["spec"]["template"]["spec"]["containers"]:
                container["startupProbe"]["failureThreshold"] = math.ceil(cold_timeout / 10)
        if resource["kind"] == "Deployment" and images:
            spec = resource["spec"]["template"]["spec"]
            for container in spec.get("containers", []) + spec.get("initContainers", []):
                name = container["image"].split(":")[0]
                if name in images:
                    container["image"] = images[name]
                    container["imagePullPolicy"] = "Never"
    return resources


def check_images(arch):
    for image in (
        "python:3.12-slim-bookworm",
        "node:24-alpine",
        "nginxinc/nginx-unprivileged:1.28-alpine",
        DYNAMODB,
    ):
        manifest = json.loads(
            run(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "inspect",
                    image,
                    "--format",
                    "{{json .Manifest}}",
                ],
                timeout=180,
            ).stdout
        )
        require(
            any(
                m.get("platform", {}) == {"architecture": arch, "os": "linux"}
                or (
                    m.get("platform", {}).get("architecture") == arch
                    and m.get("platform", {}).get("os") == "linux"
                )
                for m in manifest.get("manifests", [])
            ),
            f"No native linux/{arch} image: {image}",
        )
        print(f"Native linux/{arch} manifest confirmed: {image}")


def up(args, plan):
    # Check ownership again under the operation lock, before replacing any attempt report.
    """Record a new attempt; archive old evidence and fail closed on interruption."""
    require_local_docker()
    check_ownership(state(optional=True))
    warnings = list(plan["report"]["blockers"])
    for warning in warnings:
        print(f"WARNING: {warning} Deployment will still be attempted.", flush=True)
    report = {
        "profile": PROFILE,
        "cluster_uid": TARGET["cluster_uid"],
        "attempt_id": secrets.token_hex(12),
        "started_at": int(time.time()),
        "status": "in_progress",
        "stage": "report_initialization",
        "component": None,
        "preflight": plan["report"],
        "diagnostic_warnings": warnings,
        "application_ready": False,
        "review_completed": False,
        "ui_verified": False,
    }

    def stage(name, component=None):
        report.update(stage=name, component=component)
        save("startup.json", report)
        print(f"Deployment stage: {name}" + (f" ({component})" if component else ""), flush=True)

    try:
        from minikube_state import archive_reports

        archive_reports()
        if (STATE / "undeployment.json").exists():
            from minikube_state import read

            previous_cleanup = read("undeployment.json")
            save(
                "undeployment.json", previous_cleanup | {"status": "superseded", "quiesced": False}
            )
        save("startup.json", report)
        save(
            "deployment.json",
            {
                "attempt_id": report["attempt_id"],
                "status": "in_progress",
                "application_ready": False,
            },
        )
        save(
            "verification.json",
            {
                "status": "not_run",
                "reason": "deployment_started",
                "deployment_attempt_id": report["attempt_id"],
                "review_completed": False,
                "persistence_verified": False,
                "api_acceptance_passed": False,
                "ui_verified": False,
            },
        )
        timings = deploy_application(args, plan, stage)
        report.update(timings)
        report.update(status="ready", stage="complete", component=None, application_ready=True)
        save("startup.json", report)
    except (Exception, KeyboardInterrupt) as exc:
        # Record and re-raise; no failed deployment step is ever skipped or continued.
        if report["stage"] == "complete":
            report["stage"] = "report_save"
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            application_ready=False,
            error_type=type(exc).__name__,
        )
        message = (
            str(exc) if isinstance(exc, DemoError) else f"{type(exc).__name__}; details withheld"
        )
        report["error"] = message
        try:
            save("startup.json", report)
            save(
                "deployment.json",
                {
                    "attempt_id": report["attempt_id"],
                    "status": report["status"],
                    "stage": report["stage"],
                    "application_ready": False,
                },
            )
        except OSError:
            print(
                "ERROR: Could not save the failed attempt report; existing report may be stale.",
                file=sys.stderr,
            )
        if isinstance(exc, KeyboardInterrupt):
            raise
        component = f" ({report['component']})" if report["component"] else ""
        raise DemoError(
            f"Deployment failed during {report['stage']}{component}: {message}"
        ) from None
    print(json.dumps(report))
    print("Ready is not inference acceptance. Next: verify, then port-forward.")


def deploy_application(args, plan, stage):
    """Apply one checked plan under the target lock, preserving owned data and signing state.

    Build/load images, initialize DynamoDB, apply workloads and wait for readiness
    before publishing completed deployment evidence. Errors propagate to up()."""
    import boto3  # noqa: F401
    import yaml  # noqa: F401

    stage("ownership_check")
    require_local_docker()
    arch = plan["architecture"]
    owner = state(optional=True)
    ns = check_ownership(owner)
    if owner is None:
        owner = TARGET | {"owner": secrets.token_hex(24), "state_version": 2}
        save("owner.json", owner)
    port = plan.get("port") or args.port or owner.get("port", 8080)
    stage("namespace_setup")
    if ns is None:
        k(
            "create",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": NAMESPACE,
                        "labels": {LABEL: NAMESPACE},
                        "annotations": {OWNER_KEY: owner["owner"]},
                    },
                }
            ),
        )
        ns = obj("namespace", NAMESPACE)
    owner["namespace_uid"] = ns["metadata"]["uid"]
    save("owner.json", owner)
    guard_cluster()
    from minikube_state import IDENTITY, image_proof, read

    planned = {key: owner[key] for key in IDENTITY}
    planned.update(
        attempt_id=read("startup.json")["attempt_id"],
        status="planned",
        application_ready=False,
        port=port,
        architecture=arch,
        storage_class=plan["storage_class"],
        build_fingerprints=plan["fingerprints"],
        source_root=str(ROOT),
        images={},
        image_ids={},
        runtime_image_ids={},
    )
    save("plan.json", planned)
    stage("image_manifest_check")
    check_images(arch)
    tag = time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(5)
    images = {name: f"{name}:minikube-{tag}" for name in ("review-backend", "review-frontend")}
    started = time.monotonic()
    for name, context in (("review-backend", "backend"), ("review-frontend", "frontend")):
        stage("image_build", name)
        reusable = plan["reusable_images"].get(name)
        if reusable:
            print(f"Reusing verified native {name} layers with unique tag {tag}.", flush=True)
            run(["docker", "tag", reusable, images[name]])
        else:
            print(f"Building native {name}, unique tag {tag}…", flush=True)
            code = subprocess.call(
                [
                    "docker",
                    "build",
                    "--platform",
                    f"linux/{arch}",
                    "-t",
                    images[name],
                    str(ROOT / context),
                ],
                env=clean_env(),
            )
            require(code == 0, f"{name} build failed; check wheel/architecture/disk diagnostics.")
        actual = run(
            ["docker", "image", "inspect", images[name], "--format", "{{.Architecture}}"]
        ).stdout.strip()
        require(actual == arch, "Built image architecture mismatch; refusing emulation.")
        stage("image_load", name)
        # Persist the build identity before loading; compare loaded content to that exact build.
        expected_id = run(
            ["docker", "image", "inspect", images[name], "--format", "{{.Id}}"]
        ).stdout.strip()
        planned["images"][name] = images[name]
        planned["image_ids"][name] = expected_id
        save("plan.json", planned)
        mk("image", "load", "--daemon=true", images[name], timeout=900)
        local_id, runtime_id = image_proof(images[name], arch, expected_id)
        planned["images"][name] = images[name]
        planned["image_ids"][name] = local_id
        planned["runtime_image_ids"][name] = runtime_id
        save("plan.json", planned)
    stage("resource_render")
    resources = render(port, images, args.cold_timeout, plan["storage_class"])
    # Preflight ALL resource conflicts before applying any application changes.
    stage("resource_ownership_check")
    for resource in resources:
        previous = obj(resource["kind"], resource["metadata"]["name"], optional=True)
        if previous:
            owned(previous, owner["owner"])
    stage("secret_setup")
    ensure_secret(owner["owner"])
    with idle_application_update(owner["owner"]):
        initial = [
            r
            for r in resources
            if r["kind"] != "Deployment" or r["metadata"]["name"] == "review-dynamodb"
        ]
        stage("dependency_apply")
        apply(initial, owner["owner"])
        stage("dependency_readiness")
        wait_rollout("review-dynamodb", 180)
        stage("dependency_initialization")
        init_users()  # A hard sequencing gate before backend is created/updated.
        model_start = time.monotonic()
        stage("application_apply")
        apply(
            [
                r
                for r in resources
                if r["kind"] == "Deployment" and r["metadata"]["name"] != "review-dynamodb"
            ],
            owner["owner"],
        )
        stage("backend_readiness")
        wait_rollout("review-backend", args.cold_timeout)
        stage("frontend_readiness")
        wait_rollout("review-frontend", 180)
    stage("state_save")
    owner.update(
        port=port,
        images=images,
        architecture=arch,
        storage_class=plan["storage_class"],
        cni=plan["cni"],
        build_fingerprints=plan["fingerprints"],
        image_ids=planned["image_ids"],
        runtime_image_ids=planned["runtime_image_ids"],
    )
    save("owner.json", owner)
    save("deployment.json", planned | {"status": "ready", "application_ready": True})

    return {
        "build_and_deploy_seconds": round(time.monotonic() - started, 1),
        "backend_ready_seconds_including_download_if_needed": round(
            time.monotonic() - model_start, 1
        ),
    }


@contextlib.contextmanager
def idle_application_update(owner):
    """Drain before replacement and restore desired replicas after a failed update."""
    backend = obj("deployment", "review-backend", optional=True)
    if not backend or not backend["spec"].get("replicas", 1):
        yield
        return
    require_idle()
    paused = []
    try:
        for name in ("review-frontend", "review-backend"):
            if name == "review-backend":
                require_idle()
            previous = obj("deployment", name, optional=True)
            if previous and previous["spec"].get("replicas", 1):
                owned(previous, owner)
                paused.append((name, previous["metadata"]["uid"], previous["spec"]["replicas"]))
                k("scale", "deployment/" + name, "--replicas=0")
                k("wait", "--for=delete", "pod", "-l", "app=" + name, "--timeout=120s", timeout=150)
        yield
    finally:
        for name, uid, replicas in reversed(paused):
            current = obj("deployment", name)
            owned(current, owner)
            require(
                current["metadata"]["uid"] == uid,
                "Deployment identity changed; refusing replica restoration.",
            )
            if current["spec"].get("replicas") != replicas:
                k("scale", "deployment/" + name, f"--replicas={replicas}")


def require_idle(*, check_ready=False):
    """Read the global queue count from owned backend storage before any workload interruption."""
    readiness = (
        "import json, urllib.request; "
        "r=urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=10); "
        "assert r.status == 200 and json.load(r)['status'] == 'ready'; "
        if check_ready
        else ""
    )
    count = k(
        "exec",
        "deployment/review-backend",
        "-c",
        "review-backend",
        "--",
        "python",
        "-c",
        readiness
        + "import sqlite3; c=sqlite3.connect('file:/data/reviews.sqlite3?mode=ro', uri=True); "
        "q=\"SELECT count(*) FROM reviews WHERE status IN ('queued','running')\"; "
        "print(c.execute(q).fetchone()[0])",
    )
    require(count.stdout.strip() == "0", "Active/queued reviews exist; refusing interruption.")


def logs():
    # Backend SafeFormatter output only, select bounded fields again; no raw nginx/Java logs.
    raw = k("logs", "deployment/review-backend", "-c", "review-backend", "--tail=200").stdout
    events = {
        "http_request",
        "review_submitted",
        "review_started",
        "review_finished",
        "model_ready",
        "coordinator_failed",
        "model_cache_incomplete",
        "startup_stage_started",
        "startup_stage_completed",
        "startup_stage_failed",
        "inference_stuck",
        "model_generation_finished",
        "queue_rejected",
        "request_failed",
    }
    fields = {
        "timestamp",
        "level",
        "event",
        "error_code",
        "outcome",
        "validation_reason",
        "stage",
        "exception_type",
        "cache_reason",
        "http_status",
        "errno",
        "duration_ms",
        "queue_wait_ms",
        "queue_depth",
        "generated_tokens",
        "output_token_limit",
    }
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("event") in events:
            print(json.dumps({key: value[key] for key in fields if key in value}))


def main():
    from minikube_target import connected_target

    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "You manage the cluster; this script deploys the application. "
            "doctor exits nonzero for failed or incomplete diagnostics. "
            "up warns and attempts deployment despite resource/version diagnostics; "
            "target, ownership, architecture and storage requirements remain mandatory. "
            "recover-cleanup defaults to a read-only preview; deletion requires explicit target "
            "identities and --execute --purge-data --confirm-data-loss local-review-demo. "
            "See docs/guides/minikube-demo.md."
        ),
    )
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "up",
            "port-forward",
            "verify",
            "status",
            "logs",
            "stop",
            "import-state",
            "undeploy",
            "inspect-target",
            "recover-cleanup",
        ],
    )
    parser.add_argument(
        "--profile",
        help="existing running profile; required for inspect-target/recover-cleanup "
        "or when multiple clusters are running",
    )
    parser.add_argument(
        "--minikube-home",
        default=str(MINIKUBE_HOME),
        help="existing minikube state directory; defaults to MINIKUBE_HOME or ~/.minikube",
    )
    parser.add_argument(
        "--storage-class", help="existing minikube-hostpath StorageClass; default: standard"
    )
    parser.add_argument("--port", type=int, help="localhost HTTP port; set during up; default 8080")
    parser.add_argument("--cold-timeout", type=int, default=3600)
    parser.add_argument("--warm-timeout", type=int, default=600)
    parser.add_argument(
        "--skip-restart",
        action="store_true",
        help="API diagnostics only; does not pass persistence acceptance",
    )
    parser.add_argument(
        "--state-root",
        help="private shared state root; overrides LOCAL_QWEN_STATE_HOME / XDG_STATE_HOME",
    )
    parser.add_argument(
        "--from-state", help="explicit legacy checkout, state root, or target directory to import"
    )
    parser.add_argument(
        "--purge-data",
        action="store_true",
        help="delete persistent data (confirmation required); recovery also deletes namespace",
    )
    parser.add_argument(
        "--execute", action="store_true", help="recover-cleanup: execute the read-only plan"
    )
    parser.add_argument(
        "--restore-frontend",
        action="store_true",
        help="recover-cleanup: restore only this journal's paused frontend UID",
    )
    parser.add_argument(
        "--expect-cluster-uid", help="recover-cleanup: independently confirmed cluster UID"
    )
    parser.add_argument(
        "--expect-namespace-uid", help="recover-cleanup: independently confirmed namespace UID"
    )
    parser.add_argument(
        "--expect-owner", help="recover-cleanup: original resource ownership marker"
    )
    parser.add_argument(
        "--confirm-data-loss", help="required with --purge-data: type local-review-demo"
    )
    parser.add_argument(
        "--delete-timeout",
        type=int,
        default=120,
        help="cleanup: per-resource wait bound in seconds",
    )
    args = parser.parse_args()
    require(
        not args.purge_data
        or (
            args.command in {"undeploy", "recover-cleanup"} and args.confirm_data_loss == NAMESPACE
        ),
        "Data deletion requires --purge-data --confirm-data-loss local-review-demo.",
    )
    require(
        not args.execute or (args.command == "recover-cleanup" and args.purge_data),
        "Recovery execution requires --execute --purge-data --confirm-data-loss local-review-demo.",
    )
    require(
        not args.restore_frontend
        or (args.command == "recover-cleanup" and not args.execute and not args.purge_data),
        "--restore-frontend is a separate recovery action; do not combine it with deletion.",
    )
    if args.command in {"inspect-target", "recover-cleanup"}:
        require(bool(args.profile), "Recovery inspection requires an explicit --profile.")
        require(not args.from_state, "Recovery never imports or migrates state.")
    if args.command == "recover-cleanup":
        require(
            bool(args.expect_cluster_uid and args.expect_namespace_uid and args.expect_owner),
            "Recovery requires --expect-cluster-uid, --expect-namespace-uid and --expect-owner. "
            "Read identities with inspect-target first.",
        )
    require(1 <= args.delete_timeout <= 600, "Deletion timeout must be 1–600 seconds.")
    if args.command == "stop":
        print(
            "stop performs no operations. You manage the cluster using minikube commands; "
            "consider other projects on the same cluster. "
            "Use undeploy for owned application cleanup before stopping the cluster yourself."
        )
        return
    require(args.port is None or 1024 <= args.port <= 65535, "Choose a port 1024–65535.")
    require(args.cold_timeout >= 60 and args.warm_timeout >= 60, "Readiness waits must be >= 60s.")
    safe = clean_env()
    for key in list(os.environ):
        if key.startswith(("AWS_", "HF_", "MINIKUBE_")):
            del os.environ[key]
    os.environ.update(safe)
    from minikube_store import (
        maybe_import,
        operation_lock,
        private_directory,
        recover_absent_namespace,
        state_root,
    )

    global STATE_ROOT
    STATE_ROOT = state_root(args.state_root)
    with connected_target(args) as kubeconfig:
        with operation_lock() as lock:
            if args.command in {"inspect-target", "recover-cleanup"}:
                from minikube_recovery import inspect_target, recover_cleanup

                if args.command == "inspect-target":
                    inspect_target()
                else:
                    recover_cleanup(args)
                return
            maybe_import(args)
            if args.command == "import-state":
                return
            if args.command == "doctor":
                doctor(args)
                return
            if args.command == "undeploy":
                from minikube_undeploy import undeploy

                undeploy(args)
                return
            if args.command == "up":
                guard_target()
                recover_absent_namespace()
                plan = deployment_plan(args)
                private_directory(STATE)
                check_ownership(state(optional=True))
                save("kubeconfig", kubeconfig)
                up(args, plan)
                return
            owner = guard_cluster()
            if args.command == "port-forward":
                from minikube_state import verify_state

                port = verify_state(owner)["port"]
                require(
                    args.port is None or args.port == port,
                    "Port differs from ALLOWED_ORIGIN; run up --port PORT first.",
                )
                fcntl.flock(lock, fcntl.LOCK_UN)
                with forward("review-frontend", 8080, port, wait=True):
                    print(
                        f"Browser: http://localhost:{port} "
                        "(loopback only; Ctrl-C stops forwarding)",
                        flush=True,
                    )
            elif args.command == "status":
                print(
                    k(
                        "get",
                        "deployments,pods,pvc,services",
                        "-l",
                        f"{LABEL}={NAMESPACE}",
                        "-o",
                        "wide",
                    ).stdout
                )
                from minikube_target import cni_status

                print(json.dumps(cni_status(), ensure_ascii=False))
            elif args.command == "logs":
                logs()
            elif args.command == "verify":
                from minikube_verify import verify

                verify(owner, args)


def cli():
    try:
        main()
    except KeyboardInterrupt:
        print(
            "Interrupted; inspect the operation report before retrying. Cleanup may be partial.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:  # noqa: BLE001 - suppress sensitive third-party error text
        # Third-party exception strings can contain URLs, credentials or response bodies.
        message = (
            str(exc) if isinstance(exc, DemoError) else f"{type(exc).__name__}; details withheld"
        )
        print(
            f"ERROR: {message}\n"
            "Next: scripts/minikube_demo.sh status / logs; docs/guides/minikube-demo.md",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.modules.setdefault("minikube_demo", sys.modules[__name__])
    sys.exit(cli())
