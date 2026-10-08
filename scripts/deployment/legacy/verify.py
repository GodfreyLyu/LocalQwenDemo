"""Accept a previously completed, owned minikube deployment using the real model.

Called by minikube_demo under the target lock. Validate state/builds first, then
exercise HTTP/authentication/history and controlled idle-workload recreation.
This creates accounts/reviews and restarts workloads; it is not a read-only check.
"""

import json
import os
import secrets
import time
from uuid import uuid4

import httpx

from deployment.common import acceptance
from deployment.common.acceptance import MODEL as MODEL
from deployment.common.acceptance import REVISION as REVISION
from deployment.common.acceptance import (
    SOURCE,
    completed_review,
    history_contains,
    login,
    request,
)
from deployment.common.errors import DemoError, require
from deployment.legacy.context import api


def forward(*args, **kwargs):
    return api().forward(*args, **kwargs)


def guard_cluster(*args, **kwargs):
    return api().guard_cluster(*args, **kwargs)


def k(*args, **kwargs):
    return api().k(*args, **kwargs)


def obj(*args, **kwargs):
    return api().obj(*args, **kwargs)


def require_idle(*args, **kwargs):
    return api().require_idle(*args, **kwargs)


def save(*args, **kwargs):
    return api().save(*args, **kwargs)


def wait_rollout(*args, **kwargs):
    return api().wait_rollout(*args, **kwargs)


def cache_inventory(executor=None):
    return acceptance.cache_inventory(executor or backend_python)


def backend_python(code):
    return k(
        "exec", "deployment/review-backend", "-c", "review-backend", "--", "python", "-c", code
    )


def memory_sample():
    result = backend_python(
        "import json; from pathlib import Path; "
        "p=Path('/sys/fs/cgroup'); "
        "print(json.dumps({n: (p/n).read_text().strip() if (p/n).is_file() else None "
        "for n in ['memory.current','memory.peak']}))"
    )
    values = json.loads(result.stdout)
    return {
        key: int(value) if value and value.isdigit() else "not_measured"
        for key, value in values.items()
    }


def runtime_checks(owner):
    guard_cluster()
    for name in ("review-history", "review-model-cache", "review-dynamodb"):
        claim = obj("pvc", name)
        require(claim["status"]["phase"] == "Bound", "Required PVC not Bound.")
        require(
            claim["spec"]["storageClassName"] == owner["storage_class"],
            "Unexpected PVC StorageClass.",
        )
    for name in ("review-backend", "review-frontend", "review-dynamodb"):
        pods = json.loads(k("get", "pods", "-l", f"app={name}", "-o", "json").stdout)["items"]
        require(
            len(pods) == 1 and all(s["ready"] for s in pods[0]["status"]["containerStatuses"]),
            "Expected exactly one ready Pod per deployment.",
        )
        if name in ("review-backend", "review-frontend"):
            require(
                pods[0]["spec"]["containers"][0]["image"] == owner["images"][name],
                "Running application image does not match the last unique build.",
            )
            actual_id = pods[0]["status"]["containerStatuses"][0].get("imageID", "")
            require(
                actual_id.removeprefix("docker-pullable://").removeprefix("containerd://")
                == owner["runtime_image_ids"][name],
                "Running application image ID differs from the verified loaded build.",
            )
    info = json.loads(
        backend_python(
            "import json, os, platform, urllib.request\n"
            "from app.config import Settings\n"
            "s=Settings()\n"
            "assert os.getuid() == 10001 and os.getgid() == 10001\n"
            "assert all(os.access(p, os.W_OK) for p in ['/data','/models/huggingface'])\n"
            "op=urllib.request.build_opener(urllib.request.ProxyHandler({}))\n"
            "identity=json.load(op.open('http://127.0.0.1:8000/api/v1/runtime',timeout=5))\n"
            "assert identity['inference_mode']=='real' and identity['accepting_submissions']\n"
            "result={'machine':platform.machine(),'model_backend':'ollama','identity':identity}\n"
            "assert identity['inference_backend']=='ollama'\n"
            "assert identity['model_id']==s.ollama_model\n"
            "assert identity['model_revision'].startswith('sha256:')\n"
            "assert not s.ollama_model_digest or "
            "identity['model_revision']==s.ollama_model_digest\n"
            "print(json.dumps(result))"
        ).stdout
    )
    native_arch = api().native_arch

    require(native_arch(info["machine"]) == owner["architecture"], "Runtime architecture mismatch.")
    # The allowed path must work before checking the denied path; an outage is not isolation.
    backend_python(
        "import boto3; d=boto3.client('dynamodb', endpoint_url='http://review-dynamodb:8000', "
        "region_name='ap-northeast-1', aws_access_key_id='local', aws_secret_access_key='local'); "
        "t=d.describe_table(TableName='llm-review-users')['Table']; "
        "assert t['TableStatus']=='ACTIVE'; "
        "assert t['KeySchema']==[{'AttributeName':'login_id','KeyType':'HASH'}]"
    )
    allowed = k(
        "exec",
        "deployment/review-frontend",
        "-c",
        "review-frontend",
        "--",
        "wget",
        "-q",
        "-O",
        "-",
        "http://review-backend:8000/health/ready",
    )
    require(
        json.loads(allowed.stdout)["status"] == "ready", "Allowed frontend→backend path failed."
    )
    # Use a Pod IP and Python sockets: DNS/utility errors must not masquerade as isolation.
    frontend = json.loads(k("get", "pods", "-l", "app=review-frontend", "-o", "json").stdout)
    address = frontend["items"][0]["status"]["podIP"]
    import ipaddress

    ipaddress.ip_address(address)
    blocked = backend_python(
        "import socket\n"
        "try:\n"
        f"    socket.create_connection(({address!r},8080), timeout=3).close()\n"
        "except TimeoutError:\n"
        "    print('blocked')\n"
        "else:\n"
        "    print('reachable')\n"
    )
    from deployment.legacy.target import cni_status

    cni = cni_status()
    observed = blocked.stdout.strip()
    require(observed in {"blocked", "reachable"}, "Network probe result is unknown.")
    info["network_policy"] = cni | {
        "denied_path_probe": observed,
        "enforcement_verified": cni["policy_support"] == "capable_not_verified"
        and observed == "blocked",
    }
    if cni["policy_support"] == "capable_not_verified":
        require(observed == "blocked", "CNI supports NetworkPolicy but denied path is reachable.")
    if not info["network_policy"]["enforcement_verified"]:
        print(
            "Network isolation has not been verified. Using the existing CNI without modifying "
            "cluster network components."
        )
    print("Active model identity, PVCs, permissions, table and allowed network paths verified.")
    return info


def restart_idle(args):
    """Pause ingress, recheck the queue, then recreate only owned acceptance workloads.

    Restore desired replicas in finally on errors; restoration is not a passing
    persistence result and does not suppress the original failure."""
    require_idle()
    started = time.monotonic()
    # Gate new browser submissions before the second queue check. Frontend has the only host entry.
    k("scale", "deployment/review-frontend", "--replicas=0")
    try:
        k("wait", "--for=delete", "pod", "-l", "app=review-frontend", "--timeout=120s", timeout=150)
        require_idle()
        k("scale", "deployment/review-backend", "--replicas=0")
        k("wait", "--for=delete", "pod", "-l", "app=review-backend", "--timeout=120s", timeout=150)
        k("rollout", "restart", "deployment/review-dynamodb")
        wait_rollout("review-dynamodb", 180)
        k("scale", "deployment/review-backend", "--replicas=1")
        wait_rollout("review-backend", args.warm_timeout)
    finally:
        # Restore desired replica counts on error, without touching data or ignoring the failure.
        k("scale", "deployment/review-backend", "--replicas=1")
        k("scale", "deployment/review-frontend", "--replicas=1")
    wait_rollout("review-frontend", 180)
    return round(time.monotonic() - started, 1)


def verify(owner, args):
    """Validate deployment evidence, then record independent review and persistence outcomes.

    Save private account credentials separately from shareable metrics. A partial
    or failed run must never promote readiness or one completed review to full acceptance."""
    report = {
        "status": "in_progress",
        "stage": "state_validation",
        "api_acceptance_passed": False,
        "profile": api().PROFILE,
        "cluster_uid": api().TARGET["cluster_uid"],
        "review_completed": False,
        "persistence_verified": False,
        "ui_verified": False,
        "container_memory": "not_measured",
        "cold_start": "not_measured",
    }
    from deployment.legacy.state import archive_reports, verify_images, verify_state

    try:
        archive_reports()
        save("verification.json", report)
        record = verify_state(owner)
        report["deployment_attempt_id"] = record["attempt_id"]
        report["cold_start"] = json.loads((api().STATE / "startup.json").read_text())
        port = record["port"]
        require(
            getattr(args, "port", None) in (None, port),
            "Port differs from the completed deployment; run up --port PORT first.",
        )
        origin = f"http://localhost:{port}"
        report["stage"] = "image_validation"
        verify_images(record)
        report["stage"] = "runtime_validation"
        report["runtime"] = runtime_checks(owner)
        identity = report["runtime"].get("identity")
        expected_backend = record.get("model_backend")
        if expected_backend:
            require(
                report["runtime"].get("model_backend") == expected_backend,
                "Deployed model backend changed; run up again.",
            )
        cache_before = cache_inventory()
        require_idle()
        # No credentials or application mutations before all state/build/runtime gates pass.
        os.umask(0o077)
        credentials = {
            "login_id": "acceptance-" + uuid4().hex,
            "password": secrets.token_urlsafe(32),
        }
        save("acceptance-account.json", credentials)
        report["stage"] = "api_acceptance"
        with httpx.Client(
            base_url=origin, headers={"Origin": origin}, trust_env=False, timeout=15
        ) as client:
            # Own the canonical local port. A pre-existing listener is never trusted or reused.
            with forward("review-frontend", 8080, port):
                home = client.get("/")
                require(
                    home.status_code == 200 and "<html" in home.text.lower(), "Homepage failed."
                )
                for header in (
                    "content-security-policy",
                    "x-frame-options",
                    "x-content-type-options",
                    "referrer-policy",
                ):
                    require(header in home.headers, "Frontend security header missing.")
                for path in ("/health/live", "/health/ready"):
                    request(client, "GET", path, 200)
                request(client, "GET", "/api/v1/reviews", 401)
                session = request(client, "POST", "/api/v1/auth/register", 201, json=credentials)
                client.headers["X-CSRF-Token"] = session["csrf_token"]
                request(client, "POST", "/api/v1/auth/logout", 204, json={})
                login(client, credentials)
                payload = {
                    "source_code": SOURCE,
                    "language": "python",
                    "client_request_id": str(uuid4()),
                }
                request(
                    client,
                    "POST",
                    "/api/v1/reviews",
                    403,
                    json=payload,
                    headers={"X-CSRF-Token": "invalid"},
                )
                request(
                    client,
                    "POST",
                    "/api/v1/reviews",
                    403,
                    json=payload,
                    headers={"Origin": "http://untrusted.invalid"},
                )
                submitted = time.monotonic()
                job = request(client, "POST", "/api/v1/reviews", 202, json=payload)
                review_id = job["review_id"]
                report["review_id"] = review_id
                deadline = submitted + 360
                while time.monotonic() < deadline:
                    detail = request(client, "GET", f"/api/v1/reviews/{review_id}", 200)
                    if detail["status"] == "failed":
                        code = detail.get("error_code")
                        safe = (
                            code
                            if code
                            in {
                                "invalid_model_response",
                                "inference_timeout",
                                "inference_failed",
                                "interrupted",
                                "empty_model_response",
                                "ollama_unavailable",
                                "ollama_model_missing",
                                "ollama_model_mismatch",
                            }
                            else "unknown_failure"
                        )
                        report["failure_code"] = safe
                        raise DemoError(
                            f"Real review failed: {safe}; no blind retry or quality relaxation."
                        )
                    if detail["status"] == "completed":
                        completed_review(detail, identity)
                        break
                    time.sleep(2)
                else:
                    raise DemoError(
                        "Review polling timed out; no success claimed and no Pods restarted."
                    )
                report.update(
                    review_completed=True, review_seconds=round(time.monotonic() - submitted, 1)
                )
                result_before = detail["review_result"]
                report["container_memory"] = memory_sample()
                history_contains(client, review_id, identity)
                request(client, "POST", "/api/v1/auth/logout", 204, json={})
                login(client, credentials)
                history_contains(client, review_id, identity)
                with httpx.Client(
                    base_url=origin, headers={"Origin": origin}, trust_env=False
                ) as other:
                    different = {
                        "login_id": "isolation-" + uuid4().hex,
                        "password": secrets.token_urlsafe(32),
                    }
                    request(other, "POST", "/api/v1/auth/register", 201, json=different)
                    request(other, "GET", f"/api/v1/reviews/{review_id}", 404)
                    require(
                        not request(other, "GET", "/api/v1/reviews", 200)["items"],
                        "Cross-user history leak.",
                    )
            if not args.skip_restart:
                report["stage"] = "persistence_acceptance"
                report["warm_restart_seconds"] = restart_idle(args)
                require(
                    cache_inventory() == cache_before, "Model cache files changed across restart."
                )
                with forward("review-frontend", 8080, port):
                    # Same cookie survives; this also proves the signing Secret was reused.
                    request(client, "GET", "/api/v1/auth/me", 200)
                    persisted = history_contains(client, review_id, identity)
                    require(
                        persisted["review_result"] == result_before,
                        "Persisted review body changed across restart.",
                    )
                    login(client, credentials)
                    history_contains(client, review_id, identity)
                after = runtime_checks(owner)
                require(after.get("identity") == identity, "Model identity changed across restart.")
                report["persistence_verified"] = True
            report["api_acceptance_passed"] = (
                report["review_completed"] and report["persistence_verified"]
            )
            report.update(
                status="passed" if report["api_acceptance_passed"] else "partial", stage="complete"
            )
            print(json.dumps(report, ensure_ascii=False))
            print("Browser UI is NOT verified. Open port-forward for manual UI acceptance.")
            require(
                not args.skip_restart,
                "Partial API-only verification; persistence acceptance was skipped.",
            )
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            api_acceptance_passed=False,
            error_type=type(exc).__name__,
        )
        if isinstance(exc, DemoError):
            report["error"] = str(exc)
        raise
    finally:
        save("verification.json", report)
