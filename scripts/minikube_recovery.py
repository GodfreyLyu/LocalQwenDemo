"""Explicit, fail-closed data abandonment when local ownership records are lost.

This is not adoption. No owner, deployment or acceptance record is created or edited.
All mutations use the same target lock and queue/deletion guards as normal undeploy.
"""

import json
import re
from urllib.parse import quote

import minikube_undeploy as teardown
from minikube_state import api
from minikube_store import private_directory

JOURNAL = "recovery-cleanup.json"
IDENTITY = ("profile", "minikube_home", "cluster_uid", "namespace_uid", "owner")
DERIVED = {
    "replicasets.apps": ("ReplicaSet", "apps/v1", "deployments.apps"),
    "pods": ("Pod", "v1", "replicasets.apps"),
    "endpointslices.discovery.k8s.io": ("EndpointSlice", "discovery.k8s.io/v1", "services"),
    "endpoints": ("Endpoints", "v1", "services"),
}


def inspect_target():
    """Read only public identities through the verified target's temporary kubeconfig."""
    d = api()
    d.guard_target()
    ns = d.obj("namespace", d.NAMESPACE, optional=True)
    print(
        json.dumps(
            {
                "profile": d.PROFILE,
                "minikube_home": d.TARGET["minikube_home"],
                "cluster_uid": d.TARGET["cluster_uid"],
                "namespace": d.NAMESPACE,
                "namespace_uid": ns["metadata"]["uid"] if ns else None,
                "owner": ns["metadata"].get("annotations", {}).get(d.OWNER_KEY) if ns else None,
            },
            indent=2,
        )
    )


def record(name):
    d = api()
    path = d.STATE / name
    if not path.exists():
        return {}
    d.require(not path.is_symlink(), "Recovery refuses symbolic state files.")
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        raise d.DemoError(
            "Unreadable recovery/ownership record; preserve it and investigate."
        ) from None
    d.require(isinstance(value, dict), "Invalid recovery/ownership record; preserve it.")
    return value


def check_records(expected):
    d = api()
    # Validate present identity fields without interpreting historical outcomes as current success.
    for name in (
        "owner.json",
        "plan.json",
        "deployment.json",
        "startup.json",
        "verification.json",
        "undeployment.json",
        JOURNAL,
    ):
        value = record(name)
        for key in IDENTITY:
            d.require(
                key not in value or value[key] == expected[key],
                f"State identity conflict: {name}.{key}; recovery stopped; records retained.",
            )
        if name == "owner.json" and all(value.get(k) == expected[k] for k in IDENTITY):
            raise d.DemoError(
                "Trusted ownership state exists; use normal undeploy --purge-data "
                "--confirm-data-loss local-review-demo instead."
            )
    previous = record(JOURNAL)
    if previous:
        d.require(
            all(previous.get(k) == expected[k] for k in IDENTITY)
            and previous.get("authorized_data_loss") is True,
            "Recovery journal identity/authorization is incomplete; inspect it first.",
        )
    return previous


def namespace(expected, *, optional=False):
    d = api()
    d.guard_target()
    d.require(d.TARGET["cluster_uid"] == expected["cluster_uid"], "Cluster UID mismatch.")
    ns = d.obj("namespace", d.NAMESPACE, optional=optional)
    if ns:
        d.require(ns["metadata"]["uid"] == expected["namespace_uid"], "Namespace UID mismatch.")
        d.owned(ns, expected["owner"])
    return ns


def system_resource(row):
    """Require controller provenance and known public content, not just a familiar name."""
    d = api()
    m = row["metadata"]
    if m.get("ownerReferences") or m.get("annotations", {}).get(d.OWNER_KEY):
        return False
    managers = {v.get("manager") for v in m.get("managedFields", [])}
    if not managers or not managers <= {"kube-controller-manager", "kube-apiserver"}:
        return False
    if (row["resource"], m["name"]) == ("serviceaccounts", "default"):
        value = d.obj("serviceaccount", "default")
        return (
            value["metadata"]["uid"] == m["uid"]
            and not value.get("secrets")
            and not value.get("imagePullSecrets")
            and "automountServiceAccountToken" not in value
        )
    if (row["resource"], m["name"]) == ("configmaps", "kube-root-ca.crt"):
        value = d.obj("configmap", m["name"])
        ca = d.MINIKUBE_HOME / "ca.crt"
        return (
            value["metadata"]["uid"] == m["uid"]
            and ca.is_file()
            and value.get("data") == {"ca.crt": ca.read_text()}
            and not value.get("binaryData")
        )
    return False


def classify(rows, marker):
    """UID edges must name the actual parent kind, version and name. Labels grant nothing."""
    d = api()
    by_uid = {r["metadata"]["uid"]: r for r in rows}
    d.require(
        len(by_uid) == len(rows), "Duplicate resource identities in discovery; inspect API types."
    )
    trusted = {
        uid
        for uid, r in by_uid.items()
        if r["resource"] in teardown.KINDS
        and r["metadata"].get("annotations", {}).get(d.OWNER_KEY) == marker
        and not r["metadata"].get("ownerReferences")
    }
    roots = set(trusted)
    while True:
        before = set(trusted)
        for uid, r in by_uid.items():
            if r["resource"] not in DERIVED:
                continue
            refs = r["metadata"].get("ownerReferences", [])
            if (
                len(refs) != 1
                or refs[0].get("uid") not in trusted
                or r["metadata"].get("annotations", {}).get(d.OWNER_KEY) not in (None, marker)
            ):
                continue
            parent = by_uid[refs[0]["uid"]]
            required = DERIVED[r["resource"]][2]
            parent_type = teardown.KINDS.get(required) or DERIVED.get(required)
            ref = refs[0]
            if (
                parent["resource"] == required
                and ref.get("name") == parent["metadata"]["name"]
                and ref.get("kind") == parent_type[0]
                and ref.get("apiVersion") == parent_type[1]
                and ref.get("controller") is True
            ):
                trusted.add(uid)
        if before == trusted:
            break
    system = {uid for uid, r in by_uid.items() if uid not in trusted and system_resource(r)}
    return roots, trusted, system


def storage_policies(rows):
    """Read claim binding and reclaim policy only; never inspect volume paths or Secret data."""
    d = api()
    result = []
    for row in rows:
        if row["resource"] != "persistentvolumeclaims":
            continue
        m = row["metadata"]
        claim = d.obj("persistentvolumeclaim", m["name"])
        d.require(claim["metadata"]["uid"] == m["uid"], "PVC identity changed during inspection.")
        volume = claim.get("spec", {}).get("volumeName")
        item = teardown.identity(row) | {
            "pv": volume,
            "reclaim_policy": "unbound",
            "underlying_data_erasure": "not_verified",
        }
        if volume:
            # Project only nonsensitive PV fields on the server.
            output = d.k(
                "get",
                "pv",
                volume,
                "-o",
                'jsonpath={.metadata.uid}{"\\n"}'
                '{.spec.claimRef}{"\\n"}{.spec.persistentVolumeReclaimPolicy}',
            ).stdout
            uid, raw_ref, policy = output.splitlines()
            ref = json.loads(raw_ref)
            d.require(
                uid
                and ref.get("uid") == m["uid"]
                and ref.get("name") == m["name"]
                and ref.get("namespace") == d.NAMESPACE
                and policy in {"Delete", "Retain", "Recycle"},
                "PV binding/reclaim policy could not be verified; cleanup refused.",
            )
            item.update(pv_uid=uid, reclaim_policy=policy)
        else:
            d.require(
                claim.get("status", {}).get("phase") == "Pending",
                "PVC binding is unknown; cleanup refused.",
            )
        result.append(item)
    return result


def snapshot(row):
    """Persist only the evidence needed to validate residual owner chains on a retry."""
    m = row["metadata"]
    return {
        "resource": row["resource"],
        "metadata": {
            key: m[key]
            for key in ("name", "uid", "resourceVersion", "annotations", "ownerReferences")
            if key in m
        },
    }


def inventory_plan(expected, previous):
    d = api()
    namespace(expected)
    rows = teardown.inventory()  # Discovery and every list must succeed; no best-effort lists.
    known = previous.get("verified", [])
    prior = {(r["resource"], r["name"]): r["uid"] for r in previous.get("observed", [])}
    prior.update({(r["resource"], r["metadata"]["name"]): r["metadata"]["uid"] for r in known})
    for r in rows:
        old = prior.get((r["resource"], r["metadata"]["name"]))
        d.require(
            not old or old == r["metadata"]["uid"],
            "Recovery resource was replaced; refusing same-name replacement.",
        )
    # Deleted parent identities are usable only as chain evidence from this recovery journal.
    present = {r["metadata"]["uid"] for r in rows}
    historical = [
        r
        for r in known
        if r["metadata"]["uid"] not in present
        and r["resource"] in (set(teardown.KINDS) | set(DERIVED))
    ]
    roots, trusted, system = classify(rows + historical, expected["owner"])
    unknown = [teardown.identity(r) for r in rows if r["metadata"]["uid"] not in trusted | system]
    candidates = [r for r in rows if r["metadata"]["uid"] in roots]
    return rows, candidates, unknown, trusted


def confirm_stopped(rows, report):
    """A completed queue fence is invalid if an external writer restarts the workload."""
    d = api()
    if report.get("quiesced") is not True:
        return
    for name in ("review-backend", "review-frontend"):
        deployment = d.obj("deployment", name, optional=True)
        if (
            name == "review-backend"
            and deployment is None
            and any(
                row["resource"] == "persistentvolumeclaims"
                and row["metadata"]["name"] == "review-history"
                for row in rows
            )
        ):
            deletions = report.get("results", []) + [report.get("deleting", {})]
            d.require(
                any(item.get("uid") == report.get("backend_uid") for item in deletions),
                "Backend disappeared without a recorded deletion intent; queue proof is unknown.",
            )
        if deployment:
            d.require(
                deployment["spec"].get("replicas", 1) == 0,
                "Workload restarted after queue fencing; cleanup stopped.",
            )
            uid = (
                report.get("backend_uid")
                if name == "review-backend"
                else report.get("frontend", {}).get("uid")
            )
            generation = (
                report.get("backend_stopped_generation")
                if name == "review-backend"
                else report.get("frontend", {}).get("stopped_generation")
            )
            d.require(
                deployment["metadata"]["uid"] == uid
                and type(generation) is int
                and deployment["metadata"].get("generation") == generation,
                "Stopped workload identity/generation changed; queue proof is no longer valid.",
            )
    # At this point both application workloads must have no descendants. DynamoDB may run
    # until controller deletion, but another backend Pod must never write into the history PVC.
    descendants = {report.get("backend_uid"), report.get("frontend", {}).get("uid")}
    for _ in range(3):
        descendants |= {
            r["metadata"]["uid"]
            for r in rows
            if any(
                ref.get("uid") in descendants for ref in r["metadata"].get("ownerReferences", [])
            )
        }
    d.require(
        not any(r["resource"] == "pods" and r["metadata"]["uid"] in descendants for r in rows),
        "Application Pods appeared after queue fencing; cleanup stopped.",
    )


def recover_cleanup(args):
    d = api()
    d.require(
        args.profile
        and args.expect_cluster_uid
        and args.expect_namespace_uid
        and args.expect_owner,
        "Recovery requires explicit --profile, --expect-cluster-uid, --expect-namespace-uid "
        "and --expect-owner; use inspect-target to read identities first.",
    )
    d.require(
        re.fullmatch(r"[0-9a-f]{48}", args.expect_owner) is not None,
        "Expected owner must be the original 48-character hexadecimal marker.",
    )
    d.require(
        not args.execute or (args.purge_data and args.confirm_data_loss == d.NAMESPACE),
        "Execution requires --execute --purge-data --confirm-data-loss local-review-demo.",
    )
    expected = {
        "profile": d.PROFILE,
        "minikube_home": d.TARGET["minikube_home"],
        "cluster_uid": args.expect_cluster_uid,
        "namespace_uid": args.expect_namespace_uid,
        "owner": args.expect_owner,
    }
    previous = check_records(expected)
    ns = namespace(expected, optional=True)
    if args.restore_frontend:
        d.require(ns is not None, "Namespace is absent; frontend cannot be restored.")
        restore_frontend(expected, previous)
        return
    if not ns:
        d.require(
            previous.get("namespace_delete_requested") is True,
            "Namespace is absent without this recovery's deletion evidence; no success claimed.",
        )
        if args.execute:
            previous.update(
                status="cleaned", stage="complete", namespace_deleted=True, remaining=[]
            )
            d.save(JOURNAL, previous)
        print(
            "Confirmed original namespace is absent. Cleanup complete; you may run up explicitly."
        )
        return
    rows, candidates, unknown, trusted = inventory_plan(expected, previous)
    policies = storage_policies([r for r in rows if r["metadata"]["uid"] in trusted])
    print(
        json.dumps(
            expected
            | {
                "status": "blocked" if unknown else "preview",
                "verified_resources": [
                    teardown.identity(r) for r in rows if r["metadata"]["uid"] in trusted
                ],
                "unknown_resources": unknown,
                "system_resources": [
                    teardown.identity(row)
                    for row in rows
                    if row["metadata"]["uid"] not in trusted
                    and teardown.identity(row) not in unknown
                ],
                "queue_state": "not_measured",
                "storage": policies,
            },
            indent=2,
        )
    )
    print(
        "Data abandonment: DELETE accounts, history, signing Secret, model cache, all three PVCs "
        "and this namespace. PV/host-directory erasure is not verified or performed "
        "by this command."
    )
    d.require(
        not unknown,
        "Unknown resources block namespace cleanup; inspect listed identities. "
        "Names/labels alone never authorize deletion.",
    )
    if not args.execute:
        print(
            "Read-only preview. Queue/drain eligibility is checked again under a write fence "
            "on execution."
        )
        return
    private_directory(d.STATE)
    report = (
        previous
        | expected
        | {
            "status": "in_progress",
            "stage": "queue_check",
            "authorized_data_loss": True,
            "namespace_deleted": False,
            "storage": policies,
            "results": previous.get("results", []),
            "remaining": [teardown.identity(r) for r in rows],
            "observed": previous.get("observed", []) + [teardown.identity(r) for r in rows],
        }
    )

    pending = previous.get("deleting")
    if pending and not any(row["metadata"]["uid"] == pending["uid"] for row in rows):
        if not any(result["uid"] == pending["uid"] for result in report["results"]):
            report["results"].append(pending | {"status": "absent"})

    def checkpoint():
        d.save(JOURNAL, report)

    def validate():
        current, _, conflicts, verified = inventory_plan(expected, report)
        d.require(not conflicts, "Unknown resources appeared during cleanup; namespace retained.")
        confirm_stopped(current, report)
        # Remember all proven chains before deleting their roots, but never unknown metadata.
        saved = {r["metadata"]["uid"]: r for r in report.get("verified", [])}
        saved.update(
            {r["metadata"]["uid"]: snapshot(r) for r in current if r["metadata"]["uid"] in verified}
        )
        report["verified"] = list(saved.values())
        report["remaining"] = [teardown.identity(r) for r in current]
        observed = {(r["resource"], r["name"]): r for r in report["observed"]}
        observed.update(
            {(r["resource"], r["metadata"]["name"]): teardown.identity(r) for r in current}
        )
        report["observed"] = list(observed.values())
        checkpoint()
        return current

    # Persist the initial plan before any mutation; subsequent validation pins every initial UID.
    report["verified"] = previous.get("verified", []) + [
        snapshot(r)
        for r in rows
        if r["metadata"]["uid"] in trusted
        and r["metadata"]["uid"] not in {x["metadata"]["uid"] for x in previous.get("verified", [])}
    ]
    checkpoint()
    try:
        teardown.quiesce(
            expected, report, args.delete_timeout, checkpoint=checkpoint, validate=validate
        )
        report["stage"] = "resource_deletion"
        checkpoint()
        candidates.sort(
            key=lambda r: (
                r["resource"] != "deployments.apps",
                r["resource"] in teardown.DATA,
                r["metadata"]["name"],
            )
        )
        for item in candidates:
            validate()
            report["deleting"] = teardown.identity(item)
            checkpoint()
            outcome = teardown.delete_one(item, expected, args.delete_timeout)
            report["results"].append(teardown.identity(item) | {"status": outcome})
            report["remaining"] = [
                r for r in report["remaining"] if r["uid"] != item["metadata"]["uid"]
            ]
            if (
                item["resource"] == "deployments.apps"
                and item["metadata"]["name"] == "review-frontend"
            ):
                report.setdefault("frontend", {"uid": item["metadata"]["uid"]})["status"] = "absent"
            checkpoint()
        validate()
        ns = namespace(expected)
        report.update(stage="namespace_deletion", namespace_delete_requested=True)
        checkpoint()
        teardown.delete_checked(
            ns,
            "namespace",
            d.NAMESPACE,
            "/api/v1/namespaces/" + quote(d.NAMESPACE, safe=""),
            args.delete_timeout,
        )
        report.update(status="cleaned", stage="complete", namespace_deleted=True, remaining=[])
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error_type=type(exc).__name__,
        )
        try:
            present = namespace(expected, optional=True)
            report["remaining"] = (
                [teardown.identity(r) for r in teardown.inventory()] if present else []
            )
            report["remaining_status"] = "measured"
            if present and report.get("frontend"):
                current = d.obj("deployment", "review-frontend", optional=True)
                report["frontend"]["status"] = (
                    "absent"
                    if current is None
                    else "identity_changed"
                    if current["metadata"]["uid"] != report["frontend"]["uid"]
                    else "paused"
                    if current["spec"].get("replicas", 1) == 0
                    else "running"
                )
        except Exception:
            report["remaining_status"] = "not_measured"
            if report.get("frontend"):
                report["frontend"]["status"] = "not_measured"
        # A failure message can include external content. Store only the safe exception type.
        print(
            "Recovery incomplete. See recovery-cleanup.json for remaining identities and frontend "
            "pause status. Retry the same confirmed target after resolving the cause; "
            "do not edit ownership."
        )
        raise
    finally:
        checkpoint()
    print(
        "Confirmed namespace cleanup complete. Run up explicitly for a new deployment. "
        "No deployment, inference or browser acceptance success is claimed."
    )


def restore_frontend(expected, report):
    """Restore this operation's ingress pause; never change backend or recreate resources."""
    d = api()
    saved = report.get("frontend", {})
    d.require(
        saved.get("uid") and type(saved.get("original_replicas")) is int,
        "No recorded frontend identity/replica intent; restoration refused.",
    )
    current = d.obj("deployment", "review-frontend", optional=True)
    d.require(current is not None, "Frontend was deleted; restoration cannot recreate it.")
    d.owned(current, expected["owner"])
    d.require(
        current["metadata"]["uid"] == saved["uid"],
        "Frontend identity changed; restoration refused.",
    )
    teardown.checked_patch(current, saved["original_replicas"])
    saved["status"] = "restored"
    saved["observed_replicas"] = saved["original_replicas"]
    d.save(JOURNAL, report)
    print(
        "Recorded frontend replicas restored with UID/resourceVersion protection. "
        "Backend readiness and cleanup completion are not implied."
    )
