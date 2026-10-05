"""Helm workload fencing, conservative data purge and namespace inventory."""

import contextlib
import json
import time

from helm_target import check_ownership, require
from minikube_helm import BOOTSTRAP
from minikube_undeploy import idle_guard


def pods_for(session, deployment):
    target = session.target
    name = deployment["metadata"]["name"]
    pods = json.loads(
        target.kubectl(
            "get",
            "pods",
            "-l",
            f"app={name},app.kubernetes.io/instance={session.release}",
            "-o",
            "json",
        ).stdout
    )["items"]
    for pod in pods:
        refs = pod["metadata"].get("ownerReferences", [])
        require(len(refs) == 1 and refs[0]["kind"] == "ReplicaSet", "Unknown Pod owner.")
        rs = target.object("replicaset", refs[0]["name"])
        require(
            rs
            and rs["metadata"]["uid"] == refs[0]["uid"]
            and any(
                ref["uid"] == deployment["metadata"]["uid"]
                for ref in rs["metadata"].get("ownerReferences", [])
            ),
            "Pod belongs to another deployment.",
        )
    return pods


def scale(session, deployment, replicas):
    session.guard()
    check_ownership(deployment, session.release, session.namespace)
    m = deployment["metadata"]
    patch = [
        {"op": "test", "path": "/metadata/uid", "value": m["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": m["resourceVersion"]},
        {"op": "replace", "path": "/spec/replicas", "value": replicas},
    ]
    session.target.kubectl("patch", "deployment", m["name"], "--type=json", "-p", json.dumps(patch))


def wait_stopped(session, deployment):
    deadline = time.monotonic() + session.args.delete_timeout
    while pods_for(session, deployment):
        require(time.monotonic() < deadline, "Pods did not terminate; cleanup is incomplete.")
        time.sleep(1)


@contextlib.contextmanager
def quiesced(session, *, restore):
    """Fence the SQLite queue before stopping the backend; restore only our original UIDs."""
    target = session.target
    backend = target.object("deployment", "review-backend")
    if not backend:
        yield
        return
    check_ownership(backend, session.release, session.namespace)
    paused = []
    success = False
    try:
        frontend = target.object("deployment", "review-frontend")
        if frontend:
            check_ownership(frontend, session.release, session.namespace)
            paused.append((frontend, frontend["spec"]["replicas"]))
            scale(session, frontend, 0)
            wait_stopped(session, frontend)
        pods = pods_for(session, backend)
        if not backend["spec"]["replicas"]:
            proof = session.read("idle.json")
            require(
                not pods
                and proof
                == {
                    "backend_uid": backend["metadata"]["uid"],
                    "namespace_uid": session.namespace_uid,
                },
                "Stopped backend has no idle proof; restore and inspect it before proceeding.",
            )
        else:
            require(len(pods) == 1, "Expected one backend Pod for the queue guard.")
            pod_name = pods[0]["metadata"]["name"]
            with idle_guard(
                "pod/" + pod_name, command=lambda *a: [*target.kube, *a], env=target.env
            ):
                paused.append((backend, backend["spec"]["replicas"]))
                scale(session, backend, 0)
                wait_stopped(session, backend)
            session.save(
                "idle.json",
                {"backend_uid": backend["metadata"]["uid"], "namespace_uid": session.namespace_uid},
            )
        yield
        success = True
    finally:
        if restore or not success:
            for original, replicas in reversed(paused):
                live = target.object("deployment", original["metadata"]["name"])
                if live:
                    require(
                        live["metadata"]["uid"] == original["metadata"]["uid"],
                        "Deployment replaced; refusing automatic replica restoration.",
                    )
                    if live["spec"]["replicas"] != replicas:
                        scale(session, live, replicas)
            session.save("idle.json", {})


def inventory(session):
    """Discover every namespaced resource type; return metadata only, never Secret payloads."""
    kinds = session.target.kubectl(
        "api-resources", "--namespaced=true", "--verbs=list", "-o", "name"
    ).stdout.split()
    require(kinds, "Resource discovery is empty; cannot establish namespace deletion scope.")
    rows = []
    for kind in sorted(set(kinds)):
        output = session.target.kubectl(
            "get", kind, "-o", 'jsonpath={range .items[*]}{.metadata}{"\n"}{end}'
        ).stdout
        rows += [
            {"resource": kind, "metadata": json.loads(line)}
            for line in output.splitlines()
            if line.strip()
        ]
    return rows


def owned_metadata(session, metadata):
    annotations = metadata.get("annotations", {})
    return (
        annotations.get("meta.helm.sh/release-name") == session.release
        and annotations.get("meta.helm.sh/release-namespace") == session.namespace
        and metadata.get("labels", {}).get("app.kubernetes.io/managed-by") == "Helm"
    )


def namespace_unknown(session, rows):
    roots = set()
    for row in rows:
        m = row["metadata"]
        labels = m.get("labels", {})
        if owned_metadata(session, m) or (
            row["resource"] == "secrets"
            and (
                m.get("annotations", {}).get(BOOTSTRAP) == session.release
                or (
                    labels.get("owner") == "helm"
                    and labels.get("name") == session.release
                    and m["name"].startswith("sh.helm.release.v1." + session.release + ".v")
                )
            )
        ):
            roots.add(m["uid"])
    descendants = {"pods", "replicasets.apps", "endpoints", "endpointslices.discovery.k8s.io"}
    while True:
        children = {
            r["metadata"]["uid"]
            for r in rows
            if r["resource"] in descendants
            and any(ref["uid"] in roots for ref in r["metadata"].get("ownerReferences", []))
        }
        if children <= roots:
            break
        roots |= children
    unknown = []
    for row in rows:
        m, kind = row["metadata"], row["resource"]
        # Namespace-managed defaults and transient Events do not contain application data.
        system = (
            (kind == "serviceaccounts" and m["name"] == "default" and not m.get("secrets"))
            or (kind == "configmaps" and m["name"] == "kube-root-ca.crt")
            or kind in {"events", "events.events.k8s.io"}
        )
        if m["uid"] not in roots and not system:
            unknown.append(kind + "/" + m["name"])
    return unknown


def delete_exact(session, resource, name, metadata):
    session.guard()
    # Raw DELETE carries UID/resourceVersion preconditions and cannot remove replacements.
    path = f"/api/v1/namespaces/{session.namespace}/{resource}/{name}"
    if resource == "namespaces":
        path = "/api/v1/namespaces/" + name
    options = {
        "apiVersion": "v1",
        "kind": "DeleteOptions",
        "preconditions": {"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]},
    }
    session.target.kubectl("delete", "--raw", path, "-f", "-", data=json.dumps(options))
    deadline = time.monotonic() + session.args.delete_timeout
    while True:
        if resource == "secrets":
            current = session.secret_metadata(name)
        else:
            live = session.target.object(resource, name)
            current = live["metadata"] if live else None
        if current is None:
            return
        require(current["uid"] == metadata["uid"], "Resource replaced during deletion.")
        require(time.monotonic() < deadline, "Resource deletion timed out; finalizers retained.")
        time.sleep(1)


def pending_volumes(session, claim_uids=None):
    pvs = json.loads(session.target.kubectl("get", "pv", "-o", "json").stdout)["items"]
    return [
        {
            "name": p["metadata"]["name"],
            "phase": p.get("status", {}).get("phase"),
            "reclaim": p["spec"].get("persistentVolumeReclaimPolicy"),
        }
        for p in pvs
        if p["spec"].get("claimRef", {}).get("namespace") == session.namespace
        and (claim_uids is None or p["spec"].get("claimRef", {}).get("uid") in claim_uids)
    ]


def undeploy(session):
    ns = session.guard()
    if not ns:
        pending = pending_volumes(session)
        session.save("cleanup.json", {"namespace_deleted": True, "remaining_volumes": pending})
        require(
            not pending, "Namespace absent but associated PVs remain; inspect storage recovery."
        )
        print("Namespace and associated volumes are already absent.")
        return
    info = session.release_info()
    if info:
        session.validate_release(info)
    require(
        info
        or not any(
            session.target.object("deployment", "review-" + component)
            for component in ("backend", "frontend", "dynamodb")
        ),
        "Workloads exist without a Helm release; use legacy cleanup or inspect ownership.",
    )
    if session.args.delete_namespace:
        require(
            ns["metadata"].get("annotations", {}).get(BOOTSTRAP) == session.release,
            "Namespace was not created by this bootstrap; delete it separately after inspection.",
        )
        unknown = namespace_unknown(session, inventory(session))
        require(not unknown, "Namespace contains external/unknown resources: " + ", ".join(unknown))
    # Save external references before release metadata disappears; never purge supplied data.
    prior = session.read("cleanup.json")
    external = set(prior.get("external_claims", []))
    if info:
        values = json.loads(
            session.helm("get", "values", session.release, "--all", "-o", "json").stdout
        )
        external = {
            v["existingClaim"]
            for v in values["persistence"].values()
            if isinstance(v, dict) and v.get("existingClaim")
        }
    if session.args.delete_namespace:
        require(
            not external,
            "Namespace contains explicitly supplied PVCs; retain or remove them separately.",
        )
    owned_claims = json.loads(session.target.kubectl("get", "pvc", "-o", "json").stdout)["items"]
    claims = {
        p["metadata"]["name"]: p["metadata"]
        for p in owned_claims
        if owned_metadata(session, p["metadata"]) and p["metadata"]["name"] not in external
    }
    old_claims = prior.get("claims", {}) if prior.get("status") != "purged" else {}
    for name, old in old_claims.items():
        if name in claims:
            require(
                claims[name]["uid"] == old["uid"],
                "Retained PVC was replaced; inspect before purge.",
            )
    report = {
        "claims": claims,
        "namespace_uid": session.namespace_uid,
        "status": "in_progress",
        "external_claims": sorted(external),
        "claim_uids": sorted(
            set(prior.get("claim_uids", [])) | {m["uid"] for m in claims.values()}
        ),
    }
    session.save("cleanup.json", report)
    with quiesced(session, restore=False):
        session.guard()
        if info:
            session.helm(
                "uninstall",
                session.release,
                "--wait",
                "--timeout",
                f"{session.args.delete_timeout}s",
                timeout=session.args.delete_timeout + 10,
            )
    if session.args.purge_data:
        # No Pods may still use a retained claim or signing key during data deletion.
        pods = json.loads(session.target.kubectl("get", "pods", "-o", "json").stdout)["items"]
        require(not pods, "Pods remain in namespace; refusing data purge.")
        for name, m in claims.items():
            live = session.target.object("pvc", name)
            if live:
                check_ownership(live, session.release, session.namespace)
                require(live["metadata"]["uid"] == m["uid"], "PVC replaced during cleanup.")
                delete_exact(session, "persistentvolumeclaims", name, live["metadata"])
        secrets = session.target.kubectl(
            "get", "secrets", "-o", 'jsonpath={range .items[*]}{.metadata}{"\n"}{end}'
        ).stdout
        for line in secrets.splitlines():
            m = json.loads(line)
            if m.get("annotations", {}).get(BOOTSTRAP) == session.release:
                delete_exact(session, "secrets", m["name"], m)
        (session.directory / "acceptance-account.json").unlink(missing_ok=True)
        deadline = time.monotonic() + session.args.delete_timeout
        tracked = None if session.args.delete_namespace else set(report["claim_uids"])
        pending = pending_volumes(session, tracked)
        while pending and time.monotonic() < deadline:
            time.sleep(1)
            pending = pending_volumes(session, tracked)
        report.update(remaining_volumes=pending, status="storage_pending" if pending else "purged")
        session.save("cleanup.json", report)
        require(
            not pending,
            "Application uninstalled, storage cleanup incomplete: "
            + ", ".join(p["name"] for p in pending)
            + ". No finalizers or node data were changed.",
        )
    else:
        report["status"] = "uninstalled_data_retained"
    if session.args.delete_namespace:
        unknown = namespace_unknown(session, inventory(session))
        require(not unknown, "Namespace contains external/unknown resources: " + ", ".join(unknown))
        ns = session.guard()
        delete_exact(session, "namespaces", session.namespace, ns["metadata"])
        report["namespace_deleted"] = True
    session.save("cleanup.json", report)
    print(
        "Cleanup: "
        + report["status"]
        + ("; namespace deleted" if session.args.delete_namespace else "; namespace retained")
    )
