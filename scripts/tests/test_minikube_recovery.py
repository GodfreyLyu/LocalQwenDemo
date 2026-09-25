"""Lost-state recovery in an in-memory API; real shared guards are exercised offline."""

import contextlib
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minikube_demo as d  # noqa: E402
import minikube_recovery as r  # noqa: E402
import minikube_store as store  # noqa: E402
import minikube_undeploy as u  # noqa: E402


@pytest.fixture
def scene(tmp_path, monkeypatch):
    marker = "a" * 48
    expected = dict(
        profile="test",
        minikube_home=str(tmp_path),
        cluster_uid="cluster",
        namespace_uid="namespace",
        owner=marker,
    )
    monkeypatch.setattr(d, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(d, "STATE", tmp_path / "state/targets/test/cluster")
    monkeypatch.setattr(d, "TARGET", expected)
    monkeypatch.setattr(d, "PROFILE", "test")
    monkeypatch.setattr(d, "MINIKUBE_HOME", tmp_path)
    (tmp_path / "ca.crt").write_text("public-ca")
    state = SimpleNamespace(
        rows=[],
        namespace={
            "kind": "Namespace",
            "metadata": {
                "name": d.NAMESPACE,
                "uid": "namespace",
                "resourceVersion": "1",
                "annotations": {d.OWNER_KEY: marker},
            },
        },
        calls=[],
        busy=None,
        fenced=False,
        expected=expected,
    )

    def add(resource, name, parent=None, owned=True):
        types = u.KINDS | r.DERIVED
        metadata = {
            "name": name,
            "uid": resource + ":" + name,
            "resourceVersion": "1",
            "generation": 1,
            "annotations": {d.OWNER_KEY: marker} if owned else {},
            "labels": {"app": name},
        }
        if parent:
            pm = parent["metadata"]
            metadata["ownerReferences"] = [
                {
                    "uid": pm["uid"],
                    "name": pm["name"],
                    "kind": types[parent["resource"]][0],
                    "apiVersion": types[parent["resource"]][1],
                    "controller": True,
                }
            ]
        item = {
            "resource": resource,
            "kind": types.get(resource, ("Unknown",))[0],
            "metadata": metadata,
            "spec": {"replicas": 1},
            "status": {"phase": "Pending"},
        }
        state.rows.append(item)
        return item

    state.add = add
    for name in ("review-backend", "review-frontend", "review-dynamodb"):
        deploy = add("deployments.apps", name)
        rs = add("replicasets.apps", name + "-rs", deploy, owned=False)
        pod = add("pods", name + "-pod", rs, owned=False)
        pod["metadata"]["labels"]["app"] = name
    for name in ("review-history", "review-model-cache", "review-dynamodb"):
        add("persistentvolumeclaims", name)
    add("secrets", "review-secrets")
    service = add("services", "review-backend")
    add("endpointslices.discovery.k8s.io", "backend-slice", service, owned=False)

    def obj(kind, name, *, optional=False):
        if kind.lower() == "namespace":
            value = state.namespace
        else:
            value = next(
                (
                    v
                    for v in state.rows
                    if v["kind"].lower() == kind.lower() and v["metadata"]["name"] == name
                ),
                None,
            )
        if value is None and not optional:
            raise d.DemoError("Missing offline resource")
        return copy.deepcopy(value)

    def gc(uid, *, only_pods=False):
        descendants = {uid}
        for _ in range(4):
            descendants |= {
                v["metadata"]["uid"]
                for v in state.rows
                if any(
                    ref["uid"] in descendants for ref in v["metadata"].get("ownerReferences", [])
                )
            }
        state.rows[:] = [
            v
            for v in state.rows
            if v["metadata"]["uid"] not in descendants or (only_pods and v["resource"] != "pods")
        ]

    def k(*args, **kwargs):
        state.calls.append((args, kwargs))
        assert args[0] in {"patch", "delete"}, "Only approved mutations reach the fake API"
        if args[0] == "patch":
            live = next(
                v
                for v in state.rows
                if v["resource"] == "deployments.apps" and v["metadata"]["name"] == args[2]
            )
            patch = json.loads(args[-1])
            assert patch[0]["value"] == live["metadata"]["uid"]
            assert patch[1]["value"] == live["metadata"]["resourceVersion"]
            if args[2] == "review-backend":
                assert state.fenced, "Backend must stop while the queue writer reservation is held"
            if live["spec"]["replicas"] != patch[2]["value"]:
                live["metadata"]["generation"] += 1
            live["spec"]["replicas"] = patch[2]["value"]
            live["metadata"]["resourceVersion"] = str(int(live["metadata"]["resourceVersion"]) + 1)
            if patch[2]["value"] == 0:
                gc(live["metadata"]["uid"], only_pods=True)
        else:
            assert args[1] == "--raw"
            pre = json.loads(kwargs["data"])["preconditions"]
            if args[2] == "/api/v1/namespaces/" + d.NAMESPACE:
                assert pre == {k: state.namespace["metadata"][k] for k in pre}
                state.namespace = None
                state.rows.clear()
            else:
                live = next(v for v in state.rows if v["metadata"]["uid"] == pre["uid"])
                assert pre["resourceVersion"] == live["metadata"]["resourceVersion"]
                gc(pre["uid"])
        return SimpleNamespace(stdout="")

    def idle(**kwargs):
        assert kwargs == {"check_ready": True}
        if state.busy:
            raise d.DemoError("Queue state: " + state.busy)

    @contextlib.contextmanager
    def fence(name):
        if state.busy:
            raise d.DemoError("Queue state: " + state.busy)
        state.fenced = True
        try:
            yield SimpleNamespace(poll=lambda: None)
        finally:
            state.fenced = False

    monkeypatch.setattr(d, "obj", obj)
    monkeypatch.setattr(d, "k", k)
    monkeypatch.setattr(d, "guard_target", lambda: None)
    monkeypatch.setattr(d, "require_idle", idle)
    monkeypatch.setattr(u, "inventory", lambda: copy.deepcopy(state.rows))
    monkeypatch.setattr(u, "idle_guard", fence)
    state.args = SimpleNamespace(
        profile="test",
        expect_cluster_uid="cluster",
        expect_namespace_uid="namespace",
        expect_owner=marker,
        execute=False,
        purge_data=False,
        confirm_data_loss=None,
        delete_timeout=1,
        restore_frontend=False,
    )
    return state


def execute(scene):
    scene.args.execute = scene.args.purge_data = True
    scene.args.confirm_data_loss = d.NAMESPACE
    r.recover_cleanup(scene.args)


def journal():
    return json.loads((d.STATE / r.JOURNAL).read_text())


def test_lost_state_preview_and_uid_chains(scene, capsys):
    r.recover_cleanup(scene.args)
    assert not scene.calls and not d.STATE.exists()
    text = capsys.readouterr().out
    assert '"unknown_resources": []' in text and "backend-slice" in text
    assert "unbound" in text and "Read-only preview" in text
    roots, trusted, system = r.classify(scene.rows, scene.expected["owner"])
    assert len(trusted) == len(scene.rows) and len(roots) < len(trusted) and not system


@pytest.mark.parametrize("missing", ["execute", "purge_data", "confirm_data_loss"])
def test_no_deletion_without_all_confirmation_flags(scene, missing):
    scene.args.execute = scene.args.purge_data = True
    scene.args.confirm_data_loss = d.NAMESPACE
    setattr(scene.args, missing, False if missing != "confirm_data_loss" else None)
    if missing == "execute":
        r.recover_cleanup(scene.args)
    else:
        with pytest.raises(d.DemoError, match="Execution requires"):
            r.recover_cleanup(scene.args)
    assert not scene.calls and not d.STATE.exists()


@pytest.mark.parametrize("field", ["expect_cluster_uid", "expect_namespace_uid", "expect_owner"])
def test_identity_mismatch_stops_every_mutation(scene, field):
    setattr(scene.args, field, "b" * 48)
    with pytest.raises(d.DemoError, match="mismatch|Ownership conflict"):
        execute(scene)
    assert not scene.calls


@pytest.mark.parametrize(
    "change",
    [
        "label_only",
        "wrong_ref_uid",
        "wrong_ref_name",
        "wrong_ref_kind",
        "wrong_ref_version",
        "no_controller",
        "foreign_marker",
        "cycle",
    ],
)
def test_similar_names_labels_or_invalid_owner_chain_never_authorize(scene, change):
    pod = next(v for v in scene.rows if v["resource"] == "pods")
    ref = pod["metadata"]["ownerReferences"][0]
    if change == "label_only":
        pod["metadata"].pop("ownerReferences")
    elif change == "foreign_marker":
        pod["metadata"]["annotations"][d.OWNER_KEY] = "b" * 48
    elif change == "cycle":
        ref["uid"] = pod["metadata"]["uid"]
    else:
        ref[
            {
                "wrong_ref_uid": "uid",
                "wrong_ref_name": "name",
                "wrong_ref_kind": "kind",
                "wrong_ref_version": "apiVersion",
                "no_controller": "controller",
            }[change]
        ] = "wrong"
    with pytest.raises(d.DemoError, match="Unknown resources"):
        execute(scene)
    assert not scene.calls


@pytest.mark.parametrize("kind", ["unrecognized.example.com", "secrets", "services", "pods"])
def test_unknown_resources_block_namespace_deletion(scene, kind):
    scene.add(kind, "review-other", owned=False)
    with pytest.raises(d.DemoError, match="Unknown resources"):
        execute(scene)
    assert not scene.calls


@pytest.mark.parametrize("reason", ["Forbidden", "discovery_failed", "list_failed"])
def test_inventory_failure_is_not_empty(scene, monkeypatch, reason):
    monkeypatch.setattr(u, "inventory", Mock(side_effect=d.DemoError(reason)))
    with pytest.raises(d.DemoError, match=reason):
        execute(scene)
    assert not scene.calls


@pytest.mark.parametrize("busy", ["queued", "running", "inference_draining", "not_measured"])
def test_queue_unavailable_or_busy_prevents_cleanup(scene, busy):
    scene.busy = busy
    with pytest.raises(d.DemoError, match=busy):
        execute(scene)
    assert not scene.calls and journal()["status"] == "failed"
    assert journal()["namespace_deleted"] is False


def test_execution_reuses_guard_and_does_not_write_normal_records(scene):
    store.private_directory(d.STATE)
    preserved = {
        "verification.json": {"status": "failed", "review_completed": True},
        "deployment.json": {"status": "failed"},
        "owner.json": {"owner": scene.expected["owner"]},
    }
    for name, value in preserved.items():
        d.save(name, value)
    before = {name: (d.STATE / name).read_bytes() for name in preserved}
    execute(scene)
    assert scene.namespace is None and journal()["status"] == "cleaned"
    assert journal()["quiesced"] is True and journal()["remaining"] == []
    assert all((d.STATE / name).read_bytes() == value for name, value in before.items())
    assert (d.STATE / r.JOURNAL).stat().st_mode & 0o777 == 0o600
    assert d.STATE.stat().st_mode & 0o777 == 0o700
    assert not any(
        key in journal()
        for key in ("application_ready", "review_completed", "api_acceptance_passed")
    )
    calls = len(scene.calls)
    execute(scene)
    assert len(scene.calls) == calls  # Same absent namespace, same journal: no repeated mutations.


@pytest.mark.parametrize("record_name", ["owner.json", "deployment.json", r.JOURNAL])
def test_conflicting_records_are_preserved(scene, record_name):
    store.private_directory(d.STATE)
    d.save(record_name, {"namespace_uid": "other"})
    before = (d.STATE / record_name).read_bytes()
    with pytest.raises(d.DemoError, match="State identity conflict"):
        execute(scene)
    assert not scene.calls and (d.STATE / record_name).read_bytes() == before


def test_trusted_ownership_uses_normal_undeploy(scene):
    store.private_directory(d.STATE)
    d.save("owner.json", scene.expected | {"root": "/old"})
    with pytest.raises(d.DemoError, match="normal undeploy"):
        execute(scene)
    assert not scene.calls


def test_replacement_namespace_after_success_is_rejected(scene):
    old = copy.deepcopy(scene.namespace)
    execute(scene)
    scene.namespace = old
    scene.namespace["metadata"]["uid"] = "new"
    with pytest.raises(d.DemoError, match="Namespace UID mismatch"):
        execute(scene)
    assert scene.namespace is not None


@pytest.mark.parametrize("failure", [d.DemoError("delete failed"), KeyboardInterrupt()])
def test_partial_failure_and_interruption_resume_without_false_success(scene, monkeypatch, failure):
    original = u.delete_one
    calls = 0

    def fail(item, owner, timeout):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        return original(item, owner, timeout)

    monkeypatch.setattr(u, "delete_one", fail)
    with pytest.raises(type(failure)):
        execute(scene)
    assert journal()["status"] in {"failed", "interrupted"}
    assert journal()["namespace_deleted"] is False and journal()["remaining"]
    assert journal()["results"] and journal()["quiesced"]
    execute(scene)
    assert journal()["status"] == "cleaned"


def test_replaced_resource_on_resume_is_rejected(scene, monkeypatch):
    monkeypatch.setattr(u, "delete_one", Mock(side_effect=d.DemoError("interrupted")))
    with pytest.raises(d.DemoError):
        execute(scene)
    item = next(v for v in scene.rows if v["resource"] == "secrets")
    item["metadata"]["uid"] = "replacement"
    count = len(scene.calls)
    with pytest.raises(d.DemoError, match="replaced"):
        execute(scene)
    assert len(scene.calls) == count


def test_new_request_before_fence_stops_cleanup_and_restores_ingress(scene, monkeypatch):
    original = d.require_idle
    calls = 0

    def race(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            scene.busy = "queued"
        original(**kwargs)

    monkeypatch.setattr(d, "require_idle", race)
    with pytest.raises(d.DemoError, match="queued"):
        execute(scene)
    assert not any(a[0] == "delete" for a, _ in scene.calls)
    assert journal()["frontend"]["status"] == "running"
    assert journal().get("quiesced") is not True


def test_restarted_backend_after_fence_blocks_deletion(scene, monkeypatch):
    original = u.quiesce

    def restart(*args, **kwargs):
        original(*args, **kwargs)
        next(
            v
            for v in scene.rows
            if v["resource"] == "deployments.apps" and v["metadata"]["name"] == "review-backend"
        )["spec"]["replicas"] = 1

    monkeypatch.setattr(u, "quiesce", restart)
    with pytest.raises(d.DemoError, match="restarted after queue fencing"):
        execute(scene)
    assert not any(a[0] == "delete" for a, _ in scene.calls)


def test_namespace_delete_timeout_and_finalizers_keep_recovery_evidence(scene, monkeypatch):
    original = d.k

    def blocked(*args, **kwargs):
        if args[:3] == ("delete", "--raw", "/api/v1/namespaces/" + d.NAMESPACE):
            scene.namespace["metadata"]["finalizers"] = ["example.com/wait"]
            return SimpleNamespace(stdout="")
        return original(*args, **kwargs)

    monkeypatch.setattr(d, "k", blocked)
    clock = iter(range(1000))
    monkeypatch.setattr(u.time, "monotonic", lambda: next(clock) * 2)
    with pytest.raises(d.DemoError, match="finalizers"):
        execute(scene)
    assert journal()["status"] == "failed" and journal()["namespace_delete_requested"]
    assert scene.namespace["metadata"]["finalizers"] == ["example.com/wait"]
    monkeypatch.setattr(d, "k", original)
    execute(scene)
    assert journal()["status"] == "cleaned"


def test_namespace_absent_without_journal_is_not_success(scene):
    scene.namespace = None
    with pytest.raises(d.DemoError, match="without this recovery"):
        execute(scene)
    assert not d.STATE.exists()


def test_interruption_after_namespace_delete_before_checkpoint(scene, monkeypatch):
    original = u.delete_checked

    def interrupted(current, kind, *args):
        result = original(current, kind, *args)
        if kind == "namespace":
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(u, "delete_checked", interrupted)
    with pytest.raises(KeyboardInterrupt):
        execute(scene)
    assert scene.namespace is None and journal()["status"] == "interrupted"
    execute(scene)
    assert journal()["status"] == "cleaned"


def test_recovery_uses_shared_target_lock(scene):
    with store.operation_lock():
        with pytest.raises(d.DemoError, match="Another application deployment operation"):
            with store.operation_lock():
                pytest.fail("Concurrent recovery must not enter")


def test_system_resources_need_controller_and_public_content_evidence(scene):
    sa = scene.add("serviceaccounts", "default", owned=False)
    ca = scene.add("configmaps", "kube-root-ca.crt", owned=False)
    for item in (sa, ca):
        item["metadata"]["managedFields"] = [{"manager": "kube-controller-manager"}]
    ca["data"] = {"ca.crt": "public-ca"}
    r.recover_cleanup(scene.args)
    ca["data"]["ca.crt"] = "unknown-ca"
    with pytest.raises(d.DemoError, match="Unknown resources"):
        execute(scene)
    assert not scene.calls


def test_system_names_without_provenance_are_unknown(scene):
    scene.add("serviceaccounts", "default", owned=False)
    with pytest.raises(d.DemoError, match="Unknown resources"):
        execute(scene)


def test_bound_pv_policy_is_measured_not_erasure_claim(scene, monkeypatch):
    claim = next(v for v in scene.rows if v["resource"] == "persistentvolumeclaims")
    claim["spec"]["volumeName"] = "volume"
    claim["status"]["phase"] = "Bound"
    ref = {
        "uid": claim["metadata"]["uid"],
        "name": claim["metadata"]["name"],
        "namespace": d.NAMESPACE,
    }
    monkeypatch.setattr(
        d, "k", lambda *a, **kw: SimpleNamespace(stdout="pv-uid\n" + json.dumps(ref) + "\nRetain")
    )
    policies = r.storage_policies([claim])
    assert policies[0]["reclaim_policy"] == "Retain"
    assert policies[0]["underlying_data_erasure"] == "not_verified"
    ref["uid"] = "replacement"
    with pytest.raises(d.DemoError, match="PV binding"):
        r.storage_policies([claim])


def test_explicit_frontend_restore_checks_identity(scene, monkeypatch):
    monkeypatch.setattr(u, "delete_one", Mock(side_effect=d.DemoError("failure")))
    with pytest.raises(d.DemoError):
        execute(scene)
    scene.args.execute = scene.args.purge_data = False
    scene.args.restore_frontend = True
    r.recover_cleanup(scene.args)
    assert journal()["frontend"]["status"] == "restored" and journal()["status"] == "failed"
    front = next(
        v
        for v in scene.rows
        if v["resource"] == "deployments.apps" and v["metadata"]["name"] == "review-frontend"
    )
    front["metadata"]["uid"] = "replacement"
    with pytest.raises(d.DemoError, match="identity changed"):
        r.recover_cleanup(scene.args)


def test_empty_discovery_and_permission_failure_do_not_return_empty_inventory(monkeypatch):
    monkeypatch.setattr(d, "k", lambda *a, **kw: SimpleNamespace(stdout=""))
    with pytest.raises(d.DemoError, match="discovery returned no types"):
        u.inventory()
    calls = 0

    def denied(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(stdout="secrets\npods\n")
        raise d.DemoError("Forbidden")

    monkeypatch.setattr(d, "k", denied)
    with pytest.raises(d.DemoError, match="Forbidden"):
        u.inventory()


def test_metadata_projection_excludes_secret_payload_and_arbitrary_annotations(monkeypatch):
    value = "review-secrets\tuid\t42\t" + "a" * 48 + '\t\t\tcontroller\t["hold"]'
    call = Mock(return_value=SimpleNamespace(stdout=value))
    monkeypatch.setattr(d, "k", call)
    item = d.obj("secret", "review-secrets")
    assert item["metadata"]["uid"] == "uid"
    query = call.call_args.args[-1]
    assert "{.metadata}" not in query and "{.data}" not in query and "last-applied" not in query
    assert item["metadata"]["finalizers"] == ["hold"]


@pytest.mark.parametrize("flags", [[], ["--execute"], ["--purge-data"], ["--profile", "test"]])
def test_cli_incomplete_confirmation_fails_before_target_access(monkeypatch, flags):
    import minikube_target

    target = Mock(side_effect=AssertionError("No live cluster access permitted"))
    monkeypatch.setattr(minikube_target, "connected_target", target)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "recover-cleanup", *flags])
    with pytest.raises(d.DemoError):
        d.main()
    target.assert_not_called()


def test_later_up_archives_partial_records_only_after_completed_cleanup(scene):
    store.private_directory(d.STATE)
    d.save("owner.json", {"owner": scene.expected["owner"]})
    d.save("verification.json", {"status": "failed", "review_completed": True})
    originals = {
        name: (d.STATE / name).read_bytes() for name in ("owner.json", "verification.json")
    }
    execute(scene)
    assert all((d.STATE / name).read_bytes() == value for name, value in originals.items())
    store.recover_absent_namespace()  # This is a simulated later up, never a live CLI call.
    assert not (d.STATE / "owner.json").exists()  # Normal up, not recovery, creates the new owner.
    for name, value in originals.items():
        assert any(path.read_bytes() == value for path in (d.STATE / "attempts").glob("*/" + name))
    assert not (d.STATE / r.JOURNAL).exists()


def test_later_up_cannot_retire_records_to_adopt_replacement_namespace(scene):
    original = copy.deepcopy(scene.namespace)
    execute(scene)
    scene.namespace = original
    scene.namespace["metadata"]["uid"] = "replacement"
    before = (d.STATE / r.JOURNAL).read_bytes()
    with pytest.raises(d.DemoError, match="refusing adoption"):
        store.recover_absent_namespace()
    assert (d.STATE / r.JOURNAL).read_bytes() == before


def test_ambiguous_interruption_during_shutdown_fails_closed_on_retry(scene, monkeypatch):
    original = u.wait_no_pods

    def interrupt(uid, *args):
        if uid.endswith(":review-backend"):
            raise KeyboardInterrupt()
        return original(uid, *args)

    monkeypatch.setattr(u, "wait_no_pods", interrupt)
    with pytest.raises(KeyboardInterrupt):
        execute(scene)
    assert journal()["quiesced"] is False
    with pytest.raises(d.DemoError, match="Queue cannot be checked"):
        execute(scene)
    assert not any(a[0] == "delete" for a, _ in scene.calls)


def test_namespace_resource_version_race_fails_without_claiming_cleanup(scene, monkeypatch):
    original = d.k

    def raced(*args, **kwargs):
        if args[:3] == ("delete", "--raw", "/api/v1/namespaces/" + d.NAMESPACE):
            pre = json.loads(kwargs["data"])["preconditions"]
            scene.namespace["metadata"]["resourceVersion"] = "changed"
            assert pre["resourceVersion"] != scene.namespace["metadata"]["resourceVersion"]
            raise d.DemoError("Precondition failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(d, "k", raced)
    with pytest.raises(d.DemoError, match="Precondition failed"):
        execute(scene)
    assert scene.namespace is not None and journal()["namespace_deleted"] is False


def test_unknown_resource_arriving_after_quiescence_blocks_cleanup(scene, monkeypatch):
    original = u.quiesce

    def unknown(*args, **kwargs):
        original(*args, **kwargs)
        scene.add("pods", "unrelated", owned=False)

    monkeypatch.setattr(u, "quiesce", unknown)
    with pytest.raises(d.DemoError, match="Unknown resources appeared"):
        execute(scene)
    assert not any(a[0] == "delete" for a, _ in scene.calls)
    assert journal()["frontend"]["status"] == "paused"


def test_cli_recovery_and_inspection_use_shared_lock_without_state_import(scene, monkeypatch):
    import minikube_target

    @contextlib.contextmanager
    def connected(args):
        assert args.profile == "test"
        yield "offline-private-config"

    original_lock = store.operation_lock
    entries = []

    @contextlib.contextmanager
    def locked():
        with original_lock() as lock:
            entries.append("lock")
            yield lock

    monkeypatch.setattr(minikube_target, "connected_target", connected)
    monkeypatch.setattr(store, "operation_lock", locked)
    monkeypatch.setattr(
        store, "maybe_import", Mock(side_effect=AssertionError("No state migration"))
    )
    monkeypatch.setattr(
        d, "run", Mock(side_effect=AssertionError("No lifecycle or process commands"))
    )
    for command in ("inspect-target", "recover-cleanup"):
        flags = (
            [
                "--expect-cluster-uid",
                "cluster",
                "--expect-namespace-uid",
                "namespace",
                "--expect-owner",
                scene.expected["owner"],
            ]
            if command == "recover-cleanup"
            else []
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "minikube_demo",
                command,
                "--profile",
                "test",
                "--state-root",
                str(d.STATE_ROOT),
                *flags,
            ],
        )
        d.main()
    assert entries == ["lock", "lock"] and not scene.calls


def test_queue_guard_expiry_during_inventory_stops_backend_patch(scene, monkeypatch):
    alive = True

    @contextlib.contextmanager
    def guard(name):
        yield SimpleNamespace(poll=lambda: None if alive else 1)

    def validate():
        nonlocal alive
        # Simulate an unusually slow namespace inventory inside the reserved-write interval.
        if (d.STATE / r.JOURNAL).exists() and journal().get("stage") == "backend_stop":
            alive = False

    monkeypatch.setattr(u, "idle_guard", guard)
    store.private_directory(d.STATE)
    report = {}
    with pytest.raises(d.DemoError, match="Queue guard expired"):
        u.quiesce(
            scene.expected,
            report,
            1,
            checkpoint=lambda: d.save(r.JOURNAL, report),
            validate=validate,
        )
    assert not any(a[:3] == ("patch", "deployment", "review-backend") for a, _ in scene.calls)


def test_real_kubectl_metadata_projection_against_offline_api(tmp_path):
    """Exercise Go JSONPath, not a mocked serializer, using only a loopback fixture."""
    import os
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "review-secrets",
            "namespace": d.NAMESPACE,
            "uid": "fixture",
            "resourceVersion": "42",
            "annotations": {
                d.OWNER_KEY: "a" * 48,
                "kubectl.kubernetes.io/last-applied-configuration": "private-fixture",
            },
            "managedFields": [{"manager": "offline"}],
            "finalizers": ["offline.test/wait"],
        },
        "data": {"key": "private-fixture"},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api":
                body = {
                    "kind": "APIVersions",
                    "apiVersion": "v1",
                    "versions": ["v1"],
                    "serverAddressByClientCIDRs": [],
                }
            elif path == "/apis":
                body = {"kind": "APIGroupList", "apiVersion": "v1", "groups": []}
            elif path == "/api/v1":
                body = {
                    "kind": "APIResourceList",
                    "apiVersion": "v1",
                    "groupVersion": "v1",
                    "resources": [
                        {
                            "name": "secrets",
                            "singularName": "secret",
                            "namespaced": True,
                            "kind": "Secret",
                            "verbs": ["get", "list"],
                        }
                    ],
                }
            elif path.endswith("/secrets/review-secrets"):
                body = secret
            elif path.endswith("/secrets"):
                body = {"apiVersion": "v1", "kind": "SecretList", "metadata": {}, "items": [secret]}
            else:
                self.send_error(404)
                return
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        config = tmp_path / "kubeconfig"
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
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for names, query in [
                (["review-secrets"], u.METADATA_PATH),
                ([], "{range .items[*]}" + u.METADATA_PATH + '{"\\n"}{end}'),
            ]:
                result = subprocess.run(
                    [
                        "kubectl",
                        "--kubeconfig",
                        str(config),
                        "--context",
                        "offline",
                        "--namespace",
                        d.NAMESPACE,
                        "--cache-dir",
                        str(tmp_path / "discovery"),
                        "--request-timeout=5s",
                        "get",
                        "secrets",
                        *names,
                        "--show-managed-fields=true",
                        "-o",
                        "jsonpath=" + query,
                    ],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    env=os.environ | {"NO_PROXY": "127.0.0.1"},
                )
                assert result.returncode == 0, "Offline metadata projection failed"
                assert "private-fixture" not in result.stdout
                value = u.parse_metadata(result.stdout.rstrip("\n"))
                assert value["uid"] == "fixture" and value["resourceVersion"] == "42"
                assert value["annotations"] == {d.OWNER_KEY: "a" * 48}
                assert value["managedFields"] == [{"manager": "offline"}]
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_scale_up_then_down_cannot_reuse_old_queue_proof(scene, monkeypatch):
    original = u.quiesce

    def changed_generation(*args, **kwargs):
        original(*args, **kwargs)
        backend = next(
            v
            for v in scene.rows
            if v["resource"] == "deployments.apps" and v["metadata"]["name"] == "review-backend"
        )
        backend["metadata"]["generation"] += 2
        assert backend["spec"]["replicas"] == 0  # External up/down leaves the same current count.

    monkeypatch.setattr(u, "quiesce", changed_generation)
    with pytest.raises(d.DemoError, match="generation changed"):
        execute(scene)
    assert not any(a[0] == "delete" for a, _ in scene.calls)


def test_interruption_after_controller_delete_resumes_from_intent(scene, monkeypatch):
    original = u.delete_one
    interrupted = False

    def fail_after_delete(item, *args):
        nonlocal interrupted
        result = original(item, *args)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(u, "delete_one", fail_after_delete)
    with pytest.raises(KeyboardInterrupt):
        execute(scene)
    assert journal()["deleting"]["name"] == "review-backend" and not journal()["results"]
    execute(scene)
    assert journal()["status"] == "cleaned"


def test_later_up_archival_interruption_preserves_originals_and_can_retry(scene, monkeypatch):
    store.private_directory(d.STATE)
    d.save("owner.json", {"owner": scene.expected["owner"]})
    d.save("verification.json", {"status": "failed", "review_completed": True})
    original_owner = (d.STATE / "owner.json").read_bytes()
    execute(scene)
    unlink = Path.unlink

    def interrupt(path, **kwargs):
        if path == d.STATE / "verification.json":
            raise KeyboardInterrupt()
        return unlink(path, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", interrupt)
        with pytest.raises(KeyboardInterrupt):
            store.recover_absent_namespace()
    assert (d.STATE / r.JOURNAL).exists()
    assert any(
        p.read_bytes() == original_owner for p in (d.STATE / "attempts").glob("*/owner.json")
    )
    store.recover_absent_namespace()
    assert not (d.STATE / r.JOURNAL).exists() and not (d.STATE / "owner.json").exists()
