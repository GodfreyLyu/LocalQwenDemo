"""Bounded, journaled teardown of explicitly owned application resources only."""

import contextlib
import json
import os
import select
import subprocess
import time
from urllib.parse import quote

from minikube_state import api, archive_reports, read

# Normal undeploy retains the namespace; lost-state recovery has a separate authorization gate.
KINDS = {
    "deployments.apps": ("Deployment", "apps/v1", "deployments"),
    "services": ("Service", "v1", "services"),
    "configmaps": ("ConfigMap", "v1", "configmaps"),
    "serviceaccounts": ("ServiceAccount", "v1", "serviceaccounts"),
    "networkpolicies.networking.k8s.io": (
        "NetworkPolicy",
        "networking.k8s.io/v1",
        "networkpolicies",
    ),
    "persistentvolumeclaims": ("PersistentVolumeClaim", "v1", "persistentvolumeclaims"),
    "secrets": ("Secret", "v1", "secrets"),
}
DATA = {"persistentvolumeclaims", "secrets"}

# Do not retrieve arbitrary annotations: last-applied configuration can contain Secret data.
METADATA_PATH = '{"\\t"}'.join(
    "{" + field + "}"
    for field in (
        ".metadata.name",
        ".metadata.uid",
        ".metadata.resourceVersion",
        ".metadata.annotations.local-review-demo/owner",
        ".metadata.ownerReferences",
        ".metadata.labels.app",
        ".metadata.managedFields[*].manager",
        ".metadata.finalizers",
    )
)


def parse_metadata(line):
    name, uid, rv, marker, refs, app, managers, finalizers = line.split("\t")
    api().require(bool(name and uid and rv), "Incomplete resource identity in inventory.")
    return {
        "name": name,
        "uid": uid,
        "resourceVersion": rv,
        "annotations": {api().OWNER_KEY: marker} if marker else {},
        "ownerReferences": json.loads(refs) if refs else [],
        "labels": {"app": app} if app else {},
        "managedFields": [{"manager": m} for m in managers.split()],
        "finalizers": json.loads(finalizers) if finalizers else [],
    }


def inventory():
    """List names/identities/ownership only, including unfamiliar namespaced resource types."""
    d = api()
    names = d.k("api-resources", "--namespaced=true", "--verbs=list", "-o", "name").stdout.split()
    d.require(bool(names), "Resource discovery returned no types; namespace inventory is unknown.")
    rows = []
    for resource in sorted(set(names)):
        output = d.k(
            "get",
            resource,
            "--show-managed-fields=true",
            "-o",
            "jsonpath={range .items[*]}" + METADATA_PATH + '{"\\n"}{end}',
        ).stdout
        for line in output.splitlines():
            if line.strip():
                metadata = parse_metadata(line)
                rows.append({"resource": resource, "metadata": metadata})
    return rows


def identity(item):
    return {
        "resource": item["resource"],
        "name": item["metadata"]["name"],
        "uid": item["metadata"]["uid"],
    }


def owned_tree(rows, marker):
    d = api()
    trusted = {
        r["metadata"]["uid"]
        for r in rows
        if r["metadata"].get("annotations", {}).get(d.OWNER_KEY) == marker
    }
    while True:
        children = {
            r["metadata"]["uid"]
            for r in rows
            if any(ref.get("uid") in trusted for ref in r["metadata"].get("ownerReferences", []))
            and r["metadata"].get("annotations", {}).get(d.OWNER_KEY) in (None, marker)
            and r["resource"]
            in {"pods", "replicasets.apps", "endpoints", "endpointslices.discovery.k8s.io"}
        }
        if children <= trusted:
            break
        trusted |= children
    for r in rows:
        d.require(
            not any(ref.get("uid") in trusted for ref in r["metadata"].get("ownerReferences", []))
            or r["metadata"]["uid"] in trusted,
            "Unknown dependent resource; refusing controller deletion. Inspect ownership manually.",
        )
    return trusted


def checked_patch(deployment, replicas):
    d = api()
    m = deployment["metadata"]
    patch = [
        {"op": "test", "path": "/metadata/uid", "value": m["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": m["resourceVersion"]},
        {"op": "replace", "path": "/spec/replicas", "value": replicas},
    ]
    d.k("patch", "deployment", m["name"], "--type=json", "-p", json.dumps(patch))


def wait_no_pods(deployment_uid, marker, timeout):
    d = api()
    deadline = time.monotonic() + timeout
    while True:
        rows = inventory()
        trusted = owned_tree(rows, marker)
        descendants = {deployment_uid}
        for _ in range(3):
            descendants |= {
                r["metadata"]["uid"]
                for r in rows
                if any(
                    ref.get("uid") in descendants
                    for ref in r["metadata"].get("ownerReferences", [])
                )
            }
        remaining = [
            r for r in rows if r["resource"] == "pods" and r["metadata"]["uid"] in descendants
        ]
        d.require(
            all(r["metadata"]["uid"] in trusted for r in remaining),
            "Unknown workload Pod; refusing cleanup.",
        )
        if not remaining:
            return
        d.require(
            time.monotonic() < deadline,
            "Workload stop timed out; remaining Pods retained. Retry undeploy after inspection.",
        )
        time.sleep(1)


# A SQLite writer reservation prevents new review commits through even a direct backend connection.
# The readiness check under that reservation distinguishes idle storage contention from draining.
IDLE_GUARD = r"""
import json, select, sqlite3, sys, urllib.request, urllib.error

def ready():
    try:
        response = urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response).get("status")

try:
    if ready() != (200, "ready"):
        raise RuntimeError("not_ready")
    db = sqlite3.connect("file:/data/reviews.sqlite3?mode=rw", uri=True, timeout=5)
    db.execute("BEGIN IMMEDIATE")
    query = "SELECT count(*) FROM reviews WHERE status IN ('queued','running')"
    count = db.execute(query).fetchone()[0]
    if count or ready() != (503, "storage_unavailable"):
        raise RuntimeError("not_idle")
    print("idle_guard_ready", flush=True)
    select.select([sys.stdin], [], [], 180)
    db.rollback()
    db.close()
except Exception:
    print("idle_guard_failed", flush=True)
    sys.exit(1)
"""


@contextlib.contextmanager
def idle_guard(pod):
    d = api()
    proc = subprocess.Popen(
        d.kargs("exec", "-i", pod, "-c", "review-backend", "--", "python", "-c", IDLE_GUARD),
        env=d.clean_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 45
        data = b""
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.2)[0]:
                data += os.read(proc.stdout.fileno(), 128)
            d.require(
                proc.poll() is None and b"idle_guard_failed" not in data,
                "Queue/health guard failed; active, draining or unmeasurable inference cannot "
                "be interrupted.",
            )
            if b"idle_guard_ready\n" in data:
                yield proc
                return
            d.require(len(data) < 256, "Unexpected queue guard output; details withheld.")
        raise d.DemoError("Queue guard timed out; no workloads deleted.")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        proc.stdout.close()


def quiesce(owner, report, timeout, *, checkpoint=None, validate=None):
    d = api()
    checkpoint = checkpoint or (lambda: d.save("undeployment.json", report))
    validate = validate or (lambda: None)
    validate()
    marker = owner["owner"]
    rows = inventory()
    trusted = owned_tree(rows, marker)
    history = sorted(
        r["metadata"]["uid"]
        for r in rows
        if r["resource"] == "persistentvolumeclaims" and r["metadata"]["name"] == "review-history"
    )
    backend = d.obj("deployment", "review-backend", optional=True)
    if backend:
        d.owned(backend, marker)
    if not backend or not backend["spec"].get("replicas", 1):
        d.require(
            not any(
                r["resource"] == "pods"
                and r["metadata"].get("labels", {}).get("app") == "review-backend"
                for r in rows
            ),
            "Stopped/partial deployment still has Pods; restore with up before undeploy.",
        )
        d.require(
            not history
            or (
                report.get("quiesced") is True
                and report.get("history_uids") == history
                and (not backend or report.get("backend_uid") == backend["metadata"]["uid"])
            ),
            "Queue cannot be checked in a stopped backend. Run up to restore it, then "
            "undeploy; data is retained.",
        )
        return
    d.require_idle(check_ready=True)
    frontend = d.obj("deployment", "review-frontend", optional=True)
    paused = False
    original_replicas = frontend["spec"].get("replicas", 1) if frontend else 0
    try:
        if frontend:
            d.owned(frontend, marker)
            report["stage"] = "frontend_stop"
            previous = report.get("frontend", {})
            if previous.get("uid") == frontend["metadata"]["uid"]:
                original_replicas = previous["original_replicas"]
            report["frontend"] = {
                "uid": frontend["metadata"]["uid"],
                "original_replicas": original_replicas,
                "status": "pause_requested",
                "stopped_generation": (
                    frontend["metadata"]["generation"]
                    + int(frontend["spec"].get("replicas", 1) != 0)
                    if type(frontend["metadata"].get("generation")) is int
                    else None
                ),
            }
            checkpoint()
            validate()
            checked_patch(frontend, 0)
            paused = True
            report["frontend"]["status"] = "paused"
            checkpoint()
            wait_no_pods(frontend["metadata"]["uid"], marker, timeout)
        d.require_idle(check_ready=True)
        rows = inventory()
        trusted = owned_tree(rows, marker)
        pods = [
            r
            for r in rows
            if r["resource"] == "pods"
            and r["metadata"]["uid"] in trusted
            and r["metadata"].get("labels", {}).get("app") == "review-backend"
        ]
        d.require(len(pods) == 1, "Expected exactly one owned backend Pod for the queue guard.")
        with idle_guard(pods[0]["metadata"]["name"]) as lease:
            live_pod = d.obj("pod", pods[0]["metadata"]["name"])
            d.require(
                live_pod["metadata"]["uid"] == pods[0]["metadata"]["uid"],
                "Backend Pod identity changed during queue fencing.",
            )
            current = d.obj("deployment", "review-backend")
            d.owned(current, marker)
            d.require(
                current["metadata"]["uid"] == backend["metadata"]["uid"] and lease.poll() is None,
                "Backend/queue guard changed before shutdown.",
            )
            # Persist shutdown intent before mutation; interruption is not proof of quiescence.
            report.update(
                stage="backend_stop",
                history_uids=history,
                backend_uid=current["metadata"]["uid"],
                backend_stopped_generation=(
                    current["metadata"]["generation"] + 1
                    if type(current["metadata"].get("generation")) is int
                    else None
                ),
                quiesced=False,
            )
            checkpoint()
            validate()
            d.require(lease.poll() is None, "Queue guard expired before backend shutdown.")
            checked_patch(current, 0)
            wait_no_pods(current["metadata"]["uid"], marker, min(timeout, 120))
        report["quiesced"] = True
        checkpoint()
    except BaseException:
        if paused:
            report["frontend"]["status"] = "restoration_pending"
            checkpoint()
            current = d.obj("deployment", "review-frontend", optional=True)
            if current:
                d.owned(current, marker)
                d.require(
                    current["metadata"]["uid"] == frontend["metadata"]["uid"],
                    "Frontend changed; refusing restoration.",
                )
                checked_patch(current, original_replicas)
                report["frontend"]["status"] = "restored"
            else:
                report["frontend"]["status"] = "absent"
            checkpoint()
        raise


def delete_one(item, owner, timeout):
    """Re-read every identity, send API UID/RV preconditions, then verify absence."""
    d = api()
    kind, version, plural = KINDS[item["resource"]]
    name = item["metadata"]["name"]
    d.guard_target()
    ns = d.obj("namespace", d.NAMESPACE)
    d.owned(ns, owner["owner"])
    d.require(
        ns["metadata"]["uid"] == owner["namespace_uid"],
        "Namespace identity changed; cleanup stopped.",
    )
    current = d.obj(kind, name, optional=True)
    if current is None:
        return "absent"
    d.owned(current, owner["owner"])
    m = current["metadata"]
    d.require(
        m["uid"] == item["metadata"]["uid"],
        "Resource identity changed; refusing deletion of a replacement.",
    )
    prefix = "/api/v1" if version == "v1" else "/apis/" + version
    url = f"{prefix}/namespaces/{d.NAMESPACE}/{plural}/{quote(name, safe='')}"
    return delete_checked(current, kind, name, url, timeout)


def delete_checked(current, kind, name, url, timeout):
    """Shared conditional API deletion; callers must authorize scope and ownership first."""
    d = api()
    m = current["metadata"]
    options = {
        "apiVersion": "v1",
        "kind": "DeleteOptions",
        "propagationPolicy": "Foreground",
        "preconditions": {"uid": m["uid"], "resourceVersion": m["resourceVersion"]},
    }
    d.k("delete", "--raw", url, "-f", "-", data=json.dumps(options), timeout=30)
    deadline = time.monotonic() + timeout
    while True:
        current = d.obj(kind, name, optional=True)
        if current is None:
            return "deleted"
        d.require(
            current["metadata"]["uid"] == m["uid"],
            "Resource replaced during deletion; inspect before retrying.",
        )
        d.require(
            time.monotonic() < deadline,
            f"Deletion timed out: {kind}/{name}; inspect remaining resource/finalizers. No "
            "finalizers were removed.",
        )
        time.sleep(1)


def undeploy(args):
    d = api()
    purge = args.purge_data
    d.require(
        not purge or args.confirm_data_loss == d.NAMESPACE,
        "Data deletion requires --purge-data --confirm-data-loss local-review-demo.",
    )
    d.guard_target()
    owner = d.state()  # Missing ownership is never authorization to delete.
    ns = d.obj("namespace", d.NAMESPACE, optional=True)
    if ns:
        d.check_ownership(owner)
        d.require(
            owner.get("namespace_uid") == ns["metadata"]["uid"],
            "Missing/mismatched namespace UID; cleanup refused.",
        )
    previous = read("undeployment.json") if (d.STATE / "undeployment.json").exists() else {}
    if previous:
        d.require(
            all(previous.get(k) == owner.get(k) for k in ("owner", "cluster_uid", "namespace_uid")),
            "Cleanup journal identity mismatch.",
        )
    rows = inventory() if ns else []
    trusted = owned_tree(rows, owner["owner"])
    candidates = [
        r
        for r in rows
        if r["resource"] in KINDS
        and r["metadata"].get("annotations", {}).get(d.OWNER_KEY) == owner["owner"]
        and (purge or r["resource"] not in DATA)
    ]
    expected = {(r["resource"], r["name"]): r["uid"] for r in previous.get("remaining", [])}
    for item in candidates:
        key = item["resource"], item["metadata"]["name"]
        d.require(
            key not in expected or expected[key] == item["metadata"]["uid"],
            "Cleanup journal resource was replaced; refusing deletion. Inspect ownership "
            "before recovery.",
        )
    unknown = [identity(r) for r in rows if r["metadata"]["uid"] not in trusted]
    if purge:
        d.require(
            not any(r["resource"] == "pods" and r["metadata"]["uid"] not in trusted for r in rows),
            "Unknown Pods in namespace; data cleanup refused because volume/Secret use is "
            "unverified.",
        )
    present_keys = {(r["resource"], r["metadata"]["name"]) for r in rows}
    absent = [
        r | {"status": "absent"}
        for r in previous.get("remaining", [])
        if (r["resource"], r["name"]) not in present_keys
    ]
    report = {
        "owner": owner["owner"],
        "cluster_uid": owner["cluster_uid"],
        "namespace_uid": owner.get("namespace_uid"),
        "status": "in_progress",
        "stage": "queue_check",
        "purge_data": purge,
        "namespace_deleted": False,
        "namespace_present": ns is not None,
        "unknown_resources": unknown,
        "remaining": [identity(r) for r in candidates],
        "results": absent,
        "retained": [identity(r) for r in rows if r not in candidates],
        "quiesced": previous.get("quiesced", False),
        "history_uids": previous.get("history_uids", []),
        "backend_uid": previous.get("backend_uid"),
    }
    print(
        "Undeploy scope: owned Deployments, Services, ConfigMaps, ServiceAccounts and "
        "NetworkPolicies."
    )
    print(
        "Data: "
        + (
            "DELETE owned PVCs and signing Secret (explicitly confirmed)."
            if purge
            else "RETAIN three PVCs, signing Secret, namespace and ownership/recovery records."
        )
    )
    print("Namespace is retained; unknown resources are outside cleanup scope.")
    for item in candidates:
        print(f"DELETE {item['resource']}/{item['metadata']['name']}")
    for item in rows:
        if item not in candidates and item["resource"] in DATA:
            print(f"RETAIN {item['resource']}/{item['metadata']['name']}")
    archive_reports()
    if previous:
        d.save("undeployment.previous.json", previous)
    d.save("undeployment.json", report)
    d.save("deployment.json", {"status": "undeploying", "application_ready": False})
    d.save("startup.json", {"status": "undeploying", "application_ready": False})
    d.save(
        "verification.json",
        {
            "status": "not_run",
            "reason": "undeploy_started",
            "review_completed": False,
            "persistence_verified": False,
            "api_acceptance_passed": False,
            "ui_verified": False,
        },
    )
    try:
        if ns:
            quiesce(owner, report, args.delete_timeout)
        report["stage"] = "resource_deletion"
        d.save("undeployment.json", report)
        # Controller deletion precedes storage deletion; foreground GC must complete first.
        candidates.sort(
            key=lambda r: (
                r["resource"] != "deployments.apps",
                r["resource"] in DATA,
                r["metadata"]["name"],
            )
        )
        for resource in candidates:
            item = identity(resource)
            outcome = delete_one(resource, owner, args.delete_timeout)
            report["results"].append(item | {"status": outcome})
            report["remaining"].remove(item)
            d.save("undeployment.json", report)
        residual = (
            [
                identity(r)
                for r in inventory()
                if r["resource"] in KINDS
                and r["metadata"].get("annotations", {}).get(d.OWNER_KEY) == owner["owner"]
                and (purge or r["resource"] not in DATA)
            ]
            if ns
            else []
        )
        report["remaining"] = residual
        d.require(not residual, "Owned resources remain or were recreated; cleanup is incomplete.")
        if purge:
            (d.STATE / "acceptance-account.json").unlink(missing_ok=True)
        # Keep the original random marker: purge does not authorize namespace adoption/deletion.
        report.update(
            status="purged" if purge else "undeployed",
            stage="complete",
            data_policy="delete" if purge else "preserve",
        )
        d.save("deployment.json", {"status": report["status"], "application_ready": False})
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error_type=type(exc).__name__,
        )
        if isinstance(exc, d.DemoError):
            report["error"] = str(exc)
        raise
    finally:
        d.save("undeployment.json", report)
    print(
        "Application teardown complete; cluster and namespace retained. No acceptance success "
        "is claimed."
    )
