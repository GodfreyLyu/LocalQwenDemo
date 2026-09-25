"""Teardown simulation plus a real SQLite admission-race test; no Kubernetes mutations."""

import contextlib
import copy
import io
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minikube_demo as d  # noqa: E402
import minikube_undeploy as u  # noqa: E402


@pytest.fixture
def cluster(monkeypatch, tmp_path):
    owner = dict(
        root="/old",
        profile="minikube",
        minikube_home="/home/.minikube",
        cluster_uid="cluster",
        namespace_uid="namespace",
        owner="a" * 48,
    )
    monkeypatch.setattr(d, "STATE", tmp_path)
    monkeypatch.setattr(d, "TARGET", owner | {"root": "/new"})
    d.save("owner.json", owner)
    d.save("acceptance-account.json", {"password": "offline-only"})
    d.save("verification.json", {"status": "failed", "review_completed": True})
    rows = []
    for resource, names in {
        "deployments.apps": ["review-backend", "review-frontend", "review-dynamodb"],
        "persistentvolumeclaims": ["review-history", "review-model-cache", "review-dynamodb"],
        "secrets": ["review-secrets"],
        "configmaps": ["review-config"],
        "services": ["review-backend", "review-frontend"],
    }.items():
        for name in names:
            rows.append(
                {
                    "resource": resource,
                    "metadata": {
                        "name": name,
                        "uid": resource + ":" + name,
                        "resourceVersion": "42",
                        "annotations": {d.OWNER_KEY: owner["owner"]},
                    },
                    "spec": {"replicas": 1},
                }
            )
    namespace = {
        "kind": "Namespace",
        "metadata": {
            "name": d.NAMESPACE,
            "uid": owner["namespace_uid"],
            "annotations": {d.OWNER_KEY: owner["owner"]},
        },
    }

    def obj(kind, name, **kwargs):
        if kind == "namespace":
            return namespace
        matching = [
            r
            for r in rows
            if r["metadata"]["name"] == name
            and (u.KINDS.get(r["resource"], ("Pod",))[0].lower() == kind.lower())
        ]
        return copy.deepcopy(matching[0] | {"kind": kind}) if matching else None

    commands = []

    def k(*args, **kw):
        commands.append((args, kw))
        assert args[:2] == ("delete", "--raw")
        options = json.loads(kw["data"])
        assert options["propagationPolicy"] == "Foreground"
        uid = options["preconditions"]["uid"]
        row = next(r for r in rows if r["metadata"]["uid"] == uid)
        assert options["preconditions"]["resourceVersion"] == row["metadata"]["resourceVersion"]
        assert row["resource"] != "namespaces"
        rows.remove(row)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(d, "guard_target", lambda: None)
    monkeypatch.setattr(d, "obj", obj)
    monkeypatch.setattr(d, "render", lambda *a: [])
    monkeypatch.setattr(d, "k", k)
    monkeypatch.setattr(u, "inventory", lambda: copy.deepcopy(rows))

    def quiesce(owner, report, timeout):
        report.update(quiesced=True, history_uids=["persistentvolumeclaims:review-history"])

    monkeypatch.setattr(u, "quiesce", quiesce)
    return SimpleNamespace(
        owner=owner,
        rows=rows,
        commands=commands,
        namespace=namespace,
        args=SimpleNamespace(purge_data=False, confirm_data_loss=None, delete_timeout=1),
    )


def report():
    return json.loads((d.STATE / "undeployment.json").read_text())


def test_default_cleanup_retains_data_and_ownership_and_is_idempotent(cluster):
    before = (d.STATE / "owner.json").read_bytes()
    u.undeploy(cluster.args)
    assert {r["resource"] for r in cluster.rows} == u.DATA
    assert len(cluster.rows) == 4
    assert (d.STATE / "acceptance-account.json").exists()
    assert (d.STATE / "owner.json").read_bytes() == before
    assert report()["status"] == "undeployed"
    assert report()["remaining"] == []
    assert any(
        json.loads(p.read_text())["review_completed"]
        for p in (d.STATE / "attempts").glob("*/verification.json")
    )
    current = json.loads((d.STATE / "verification.json").read_text())
    assert current["status"] == "not_run" and current["api_acceptance_passed"] is False
    count = len(cluster.commands)
    u.undeploy(cluster.args)
    assert len(cluster.commands) == count


def test_purge_requires_confirmation_and_retains_namespace_anchor(cluster):
    cluster.args.purge_data = True
    with pytest.raises(d.DemoError, match="confirm-data-loss"):
        u.undeploy(cluster.args)
    assert not cluster.commands
    cluster.args.confirm_data_loss = d.NAMESPACE
    u.undeploy(cluster.args)
    assert not cluster.rows
    assert not (d.STATE / "acceptance-account.json").exists()
    assert report()["status"] == "purged"
    assert report()["namespace_deleted"] is False
    assert (d.STATE / "owner.json").exists()


@pytest.mark.parametrize(
    "conflict", ["missing_state", "namespace_owner", "namespace_uid", "resource_owner"]
)
def test_untrusted_resources_are_not_adopted_or_deleted(cluster, conflict, monkeypatch):
    if conflict == "missing_state":
        (d.STATE / "owner.json").unlink()
    elif conflict == "namespace_owner":
        cluster.namespace["metadata"]["annotations"][d.OWNER_KEY] = "other"
    elif conflict == "namespace_uid":
        cluster.namespace["metadata"]["uid"] = "other"
    else:
        cluster.rows[0]["metadata"]["annotations"][d.OWNER_KEY] = "other"
        monkeypatch.setattr(
            d, "render", lambda *a: [{"kind": "Deployment", "metadata": {"name": "review-backend"}}]
        )
    with pytest.raises(d.DemoError):
        u.undeploy(cluster.args)
    assert not cluster.commands


def test_unknown_namespace_resource_is_retained_and_reported(cluster):
    unknown = {
        "resource": "configmaps",
        "metadata": {"name": "unrelated", "uid": "other", "resourceVersion": "1"},
    }
    cluster.rows.append(unknown)
    cluster.args.purge_data, cluster.args.confirm_data_loss = True, d.NAMESPACE
    u.undeploy(cluster.args)
    assert cluster.rows == [unknown]
    assert report()["unknown_resources"][0]["uid"] == "other"
    assert report()["namespace_deleted"] is False


def test_unknown_pod_blocks_purge_before_any_mutation(cluster):
    cluster.rows.append({"resource": "pods", "metadata": {"name": "unrelated", "uid": "other"}})
    cluster.args.purge_data, cluster.args.confirm_data_loss = True, d.NAMESPACE
    with pytest.raises(d.DemoError, match="Unknown Pods"):
        u.undeploy(cluster.args)
    assert not cluster.commands


def test_partial_failure_keeps_journal_and_retry_only_deletes_remaining(cluster, monkeypatch):
    actual = u.delete_one
    failed = False

    def delete(item, owner, timeout):
        nonlocal failed
        if item["resource"] == "configmaps" and not failed:
            failed = True
            raise d.DemoError("offline deletion failure")
        return actual(item, owner, timeout)

    monkeypatch.setattr(u, "delete_one", delete)
    with pytest.raises(d.DemoError, match="offline deletion"):
        u.undeploy(cluster.args)
    assert report()["status"] == "failed" and report()["remaining"]
    assert report()["results"] and (d.STATE / "owner.json").exists()
    u.undeploy(cluster.args)
    assert report()["status"] == "undeployed"
    ids = [json.loads(kw["data"])["preconditions"]["uid"] for _, kw in cluster.commands]
    assert len(ids) == len(set(ids))


def test_delete_timeout_keeps_finalizers_and_remaining_identity(cluster, monkeypatch):
    monkeypatch.setattr(d, "k", lambda *a, **kw: cluster.commands.append((a, kw)))
    clock = iter([0, 2])
    monkeypatch.setattr(u.time, "monotonic", lambda: next(clock))
    with pytest.raises(d.DemoError, match="finalizers"):
        u.undeploy(cluster.args)
    assert report()["status"] == "failed" and report()["remaining"]
    assert all(a[0] == "delete" for a, _ in cluster.commands)


def test_replacement_uid_is_never_deleted(cluster):
    item = copy.deepcopy(cluster.rows[0])
    cluster.rows[0]["metadata"]["uid"] = "replacement"
    with pytest.raises(d.DemoError, match="replacement"):
        u.delete_one(item, cluster.owner, 1)
    assert not cluster.commands


@pytest.mark.parametrize("reason", ["queued", "running", "inference_draining"])
def test_non_idle_cleanup_cannot_claim_success(cluster, monkeypatch, reason):
    monkeypatch.setattr(u, "quiesce", Mock(side_effect=d.DemoError(reason)))
    with pytest.raises(d.DemoError, match=reason):
        u.undeploy(cluster.args)
    assert not cluster.commands
    assert report()["status"] == "failed"
    assert not json.loads((d.STATE / "deployment.json").read_text())["application_ready"]


@pytest.mark.parametrize("status", ["idle", "queued", "running", "inference_draining"])
def test_real_sqlite_guard_blocks_racing_submission_and_rejects_busy(tmp_path, monkeypatch, status):
    import urllib.request

    database = tmp_path / "reviews.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE reviews(status TEXT)")
        if status in {"queued", "running"}:
            db.execute("INSERT INTO reviews VALUES (?)", (status,))
    calls = []

    class Response(io.BytesIO):
        def __init__(self, code, state):
            super().__init__(json.dumps({"status": state}).encode())
            self.status = code

    def ready(*args, **kwargs):
        calls.append(1)
        return (
            Response(200, "ready")
            if len(calls) == 1
            else Response(
                503,
                "inference_draining" if status == "inference_draining" else "storage_unavailable",
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", ready)
    raced = []

    def wait(*args):
        with sqlite3.connect(database, timeout=0.01) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("INSERT INTO reviews VALUES ('queued')")
        raced.append(True)
        return [], [], []

    monkeypatch.setattr(u.select, "select", wait)
    program = u.IDLE_GUARD.replace("/data/reviews.sqlite3", str(database))
    namespace = {}
    try:
        if status == "idle":
            exec(compile(program, "idle-guard", "exec"), namespace)
            assert raced == [True]
        else:
            with pytest.raises(SystemExit):
                exec(compile(program, "idle-guard", "exec"), namespace)
            assert not raced
    finally:
        if "db" in namespace:
            namespace["db"].close()


# Keep the real orchestrator available when the cluster fixture substitutes only its caller.
REAL_QUIESCE = u.quiesce


@pytest.mark.parametrize("failure", [None, "busy", "draining", "stop_timeout"])
def test_quiesce_orders_ingress_queue_guard_and_backend_stop(cluster, monkeypatch, failure):
    events = []
    pod = {
        "resource": "pods",
        "metadata": {
            "name": "backend-pod",
            "uid": "backend-pod-uid",
            "labels": {"app": "review-backend"},
            "ownerReferences": [{"uid": "deployments.apps:review-backend"}],
        },
    }
    cluster.rows.append(pod)

    def idle(**kwargs):
        events.append("queue")
        if failure == "busy":
            raise d.DemoError("queued")

    def scale(deployment, replicas):
        name = deployment["metadata"]["name"]
        events.append((name, replicas))
        next(
            r
            for r in cluster.rows
            if r["resource"] == "deployments.apps" and r["metadata"]["name"] == name
        )["spec"]["replicas"] = replicas

    def wait(uid, marker, timeout):
        if uid.endswith("review-backend"):
            if failure == "stop_timeout":
                raise d.DemoError("stop timeout")
            cluster.rows.remove(pod)

    @contextlib.contextmanager
    def guard(name):
        events.append("guard")
        if failure == "draining":
            raise d.DemoError("inference_draining")
        yield SimpleNamespace(poll=lambda: None)
        events.append("release")

    monkeypatch.setattr(d, "require_idle", idle)
    monkeypatch.setattr(u, "checked_patch", scale)
    monkeypatch.setattr(u, "wait_no_pods", wait)
    monkeypatch.setattr(u, "idle_guard", guard)
    value = {}
    if failure:
        with pytest.raises(d.DemoError):
            REAL_QUIESCE(cluster.owner, value, 1)
        assert value.get("quiesced") is not True
        if failure == "busy":
            assert events == ["queue"]
        else:
            assert events[-1] == ("review-frontend", 1)
    else:
        REAL_QUIESCE(cluster.owner, value, 1)
        assert events == [
            "queue",
            ("review-frontend", 0),
            "queue",
            "guard",
            ("review-backend", 0),
            "release",
        ]
        assert value["quiesced"] is True
        # Same stopped backend/history proof is reusable, without starting another process.
        events.clear()
        REAL_QUIESCE(cluster.owner, value, 1)
        assert not events


def test_stopped_backend_without_queue_proof_is_not_assumed_empty(cluster):
    cluster.rows[0]["spec"]["replicas"] = 0
    with pytest.raises(d.DemoError, match="Queue cannot be checked"):
        REAL_QUIESCE(cluster.owner, {}, 1)


def test_scaling_uses_uid_and_resource_version_preconditions(cluster, monkeypatch):
    call = Mock()
    monkeypatch.setattr(d, "k", call)
    u.checked_patch(cluster.rows[0], 0)
    args = call.call_args.args
    patch = json.loads(args[-1])
    assert patch[:2] == [
        {"op": "test", "path": "/metadata/uid", "value": cluster.rows[0]["metadata"]["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "42"},
    ]


def test_cli_purge_confirmation_fails_before_target_discovery(monkeypatch):
    import minikube_target

    target = Mock(side_effect=AssertionError("No cluster access permitted"))
    monkeypatch.setattr(minikube_target, "connected_target", target)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "undeploy", "--purge-data"])
    with pytest.raises(d.DemoError, match="confirm-data-loss"):
        d.main()
    target.assert_not_called()


def test_retry_refuses_same_name_replacement_from_journal(cluster):
    prior = {key: cluster.owner[key] for key in ("owner", "cluster_uid", "namespace_uid")}
    prior["remaining"] = [u.identity(cluster.rows[0])]
    d.save("undeployment.json", prior)
    cluster.rows[0]["metadata"]["uid"] = "replacement"
    with pytest.raises(d.DemoError, match="journal resource was replaced"):
        u.undeploy(cluster.args)
    assert not cluster.commands


def test_missing_namespace_is_recorded_as_absent_without_deletion(cluster, monkeypatch):
    monkeypatch.setattr(d, "obj", lambda *a, **kw: None)
    u.undeploy(cluster.args)
    assert not cluster.commands
    assert report()["namespace_present"] is False
    assert report()["results"] == []
    assert report()["data_policy"] == "preserve"


def test_foreign_dependent_blocks_controller_cleanup(cluster):
    cluster.rows.append(
        {
            "resource": "pods",
            "metadata": {
                "name": "foreign-dependent",
                "uid": "foreign",
                "annotations": {d.OWNER_KEY: "other"},
                "ownerReferences": [{"uid": "deployments.apps:review-backend"}],
            },
        }
    )
    with pytest.raises(d.DemoError, match="Unknown dependent"):
        u.undeploy(cluster.args)
    assert not cluster.commands


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
                    d.NAMESPACE,
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


def test_retry_records_previously_remaining_resource_as_absent(cluster):
    prior = {key: cluster.owner[key] for key in ("owner", "cluster_uid", "namespace_uid")}
    missing = u.identity(cluster.rows.pop(0))
    prior["remaining"] = [missing]
    d.save("undeployment.json", prior)
    u.undeploy(cluster.args)
    assert missing | {"status": "absent"} in report()["results"]
