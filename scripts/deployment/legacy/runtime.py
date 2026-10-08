"""Deploy this application into a user-started local minikube; never manage cluster lifecycle.

The CLI selects a verified target, locks its state, then dispatches diagnostics,
deployment or acceptance. Target discovery, state contracts and acceptance live
in sibling modules. Writes require matching target/resource ownership; reports
separate readiness from inference and browser acceptance.
"""

import contextlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from tooling_paths import ROOT

from deployment.common import forward as forwarding
from deployment.common.errors import DemoError, require
from deployment.common.files import atomic_json


def save(name, value):
    """Write under the caller's target lock without changing record ownership or identity."""
    atomic_json(STATE / name, value)


def forward(service, remote_port, local_port=None, *, wait=False, command=None, env=None):
    return forwarding.forward(
        service,
        remote_port,
        local_port,
        wait=wait,
        command=command or kargs,
        env=env if env is not None else clean_env(),
        start_timeout=FORWARD_START_TIMEOUT,
    )


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
        from deployment.legacy.undeploy import METADATA_PATH, parse_metadata

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


def state(optional=False):
    path = STATE / "owner.json"
    if optional and not path.exists():
        return None
    require(path.is_file(), "No application deployment state exists for this target; run up first.")
    from deployment.legacy.state import ownership, read

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


def doctor(args):
    plan = deployment_plan(args)
    require(
        not plan["report"]["blockers"],
        "Diagnostics failed: " + "; ".join(plan["report"]["blockers"]),
    )
    return plan


def deployment_plan(args):
    """Mandatory ownership/compatibility checks plus advisory resource diagnostics."""
    from deployment.legacy.target import preflight

    owner = state(optional=True)
    check_ownership(owner)
    from deployment.legacy.state import planned_options

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


def init_users():
    # Preserve the existing initializer's loopback-only restriction unchanged.
    with forward("review-dynamodb", 8000) as port:
        env = clean_env() | {"DYNAMODB_ENDPOINT_URL": f"http://127.0.0.1:{port}"}
        run([sys.executable, ROOT / "scripts/init_local_users.py"], env=env)
        from app.persistence.local_dynamodb import local_client

        db = local_client(f"http://127.0.0.1:{port}")
        table = db.describe_table(TableName="llm-review-users")["Table"]
        require(
            table["TableStatus"] == "ACTIVE"
            and table["KeySchema"] == [{"AttributeName": "login_id", "KeyType": "HASH"}],
            "DynamoDB Local table schema/status mismatch.",
        )
    print("DynamoDB Local table verified; existing accounts retained.")


def render(
    port,
    images=None,
    cold_timeout=3600,
    storage_class="standard",
    ollama_host_ip=None,
):
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
    if ollama_host_ip is not None:
        from deployment.legacy.ollama import egress_rule

        policies = [
            r
            for r in resources
            if r["kind"] == "NetworkPolicy" and r["metadata"]["name"] == "review-backend"
        ]
        require(len(policies) == 1, "Expected one backend NetworkPolicy in the minikube render.")
        policies[0]["spec"]["egress"].append(egress_rule(ollama_host_ip))
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
    from deployment.legacy import workflow

    return workflow.up(sys.modules[__name__], args, plan)


def deploy_application(args, plan, stage):
    from deployment.legacy import workflow

    return workflow.deploy_application(sys.modules[__name__], args, plan, stage)


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
    from deployment.legacy.cli import main as dispatch
    from deployment.legacy.context import using_context

    with using_context(sys.modules[__name__]):
        return dispatch(sys.modules[__name__])


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
            "Next: scripts/minikube_demo.sh legacy status / logs; docs/guides/minikube-legacy.md",
            file=sys.stderr,
        )
        return 1
    return 0


def entry():
    sys.exit(cli())


if __name__ == "__main__":
    entry()
