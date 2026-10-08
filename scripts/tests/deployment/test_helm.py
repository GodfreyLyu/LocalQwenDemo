"""Offline Helm workflow regressions: no Docker daemon, cluster or model access."""

import contextlib
import copy
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import deployment.helm.cli as cli
import deployment.helm.lifecycle as lifecycle  # noqa: E402
import pytest
from deployment.helm import diagnostics
from deployment.helm.constants import BOOTSTRAP
from deployment.helm.session import merge  # noqa: E402

from scripts.tests.support.paths import ROOT


def result(value=""):
    return SimpleNamespace(
        stdout=json.dumps(value) if not isinstance(value, str) else value, returncode=0
    )


def metadata(name, *, owned=False, bootstrap=False, uid=None):
    m = {
        "name": name,
        "namespace": "local-review-demo",
        "uid": uid or "uid-" + name,
        "resourceVersion": "1",
        "annotations": {},
        "labels": {},
    }
    if owned:
        m["annotations"].update(
            {
                "meta.helm.sh/release-name": "local-review",
                "meta.helm.sh/release-namespace": "local-review-demo",
            }
        )
        m["labels"]["app.kubernetes.io/managed-by"] = "Helm"
    if bootstrap:
        m["annotations"][BOOTSTRAP] = "local-review"
    return m


class Target:
    cluster_uid = "cluster"
    profile = "minikube"
    node = "minikube"
    env = dict(os.environ, MINIKUBE_HOME="/test/.minikube")
    kube = ["kubectl", "--context", "minikube", "-n", "local-review-demo"]
    helm = ["helm", "--kube-context", "minikube", "-n", "local-review-demo"]

    def __init__(self):
        self.calls = []
        self.objects = {("namespace", "kube-system"): {"metadata": {"uid": "cluster"}}}
        self.secret_valid = True
        self.pvs = []

    def object(self, kind, name):
        return copy.deepcopy(self.objects.get((kind, name)))

    def kubectl(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:2] == ("create", "-f"):
            resource = json.loads(kwargs["data"])
            resource["metadata"].update(
                uid="uid-" + resource["metadata"]["name"], resourceVersion="1"
            )
            self.objects[resource["kind"].lower(), resource["metadata"]["name"]] = resource
            return result()
        if args[:2] == ("get", "secret"):
            obj = self.object("secret", args[2])
            if "jsonpath={.metadata}" in args:
                return result(obj["metadata"] if obj else "")
            return result("true" if obj and self.secret_valid else "false")
        if args[:2] == ("get", "secrets"):
            return result(
                "\n".join(
                    json.dumps(v["metadata"]) for (k, _), v in self.objects.items() if k == "secret"
                )
            )
        if args[:2] == ("get", "pvc"):
            return result({"items": [v for (k, _), v in self.objects.items() if k == "pvc"]})
        if args[:2] == ("get", "pv"):
            return result({"items": self.pvs})
        if args[:2] == ("get", "pods"):
            return result({"items": []})
        if args[0] in {"wait", "rollout"}:
            return result()
        raise AssertionError(args)


@pytest.fixture
def session(tmp_path):
    args = cli.parser().parse_args(["up", "--profile", "minikube", "--state-root", str(tmp_path)])
    s = cli.Session(Target(), args)
    return s


def namespace(session):
    session.target.objects["namespace", session.namespace] = {
        "metadata": metadata(session.namespace, bootstrap=True)
    }
    session.archive_stale()


@pytest.mark.integration
@pytest.mark.security
def test_init_is_separate_idempotent_and_does_not_rotate_secret(session):
    session.init()
    first = copy.deepcopy(session.target.objects["secret", "review-secrets"])
    session.init()
    assert session.target.objects["secret", "review-secrets"] == first
    assert len([call for call, _ in session.target.calls if call[0] == "create"]) == 2
    assert not (session.directory / "values-local.json").exists()
    assert not list(session.directory.glob("*secret*"))


@pytest.mark.integration
@pytest.mark.security
def test_init_rejects_invalid_existing_secret_without_rotation(session):
    session.init()
    session.target.secret_valid = False
    calls = len(session.target.calls)
    with pytest.raises(ValueError, match="signing Secret"):
        session.init()
    assert all(c[0] != "create" for c, _ in session.target.calls[calls:])


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_values_precedence_and_local_digest_reset(session, tmp_path):
    path = tmp_path / "site.yaml"
    path.write_text(
        "config:\n  allowedOrigin: http://localhost:9000\nbackend:\n  image:\n    digest: sha256:"
        + "a" * 64
        + "\n"
    )
    session.args.values = [str(path)]
    session.args.port = 8081
    values = merge(
        session.options(),
        {"backend": {"image": {"tag": "local-test", "digest": "", "pullPolicy": "Never"}}},
    )
    rendered = session.render(values)
    backend = next(
        r
        for r in rendered
        if r["kind"] == "Deployment" and r["metadata"]["name"] == "review-backend"
    )
    assert (
        backend["spec"]["template"]["spec"]["containers"][0]["image"] == "review-backend:local-test"
    )
    assert values["config"]["allowedOrigin"] == "http://localhost:8081"
    assert not any(r["kind"] == "NetworkPolicy" for r in rendered)


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_diagnostic_render_preserves_saved_deployment_values(session):
    session.save("values-local.json", {"saved": True})
    session.render(session.options())
    assert session.read("values-local.json") == {"saved": True}


@pytest.mark.integration
def test_state_isolated_by_namespace_release_and_cluster_and_archives_stale(session):
    namespace(session)
    session.save("verification.json", {"status": "passed"})
    session.target.objects.pop(("namespace", session.namespace))
    session.namespace_uid = None  # A new command reconnects.
    session.archive_stale()
    assert not session.read("verification.json")
    assert list(session.directory.glob("archive-*/verification.json"))
    for field, value in [("namespace", "another"), ("release", "other")]:
        args = copy.copy(session.args)
        setattr(args, field, value)
        assert cli.Session(session.target, args).directory != session.directory
    target = Target()
    target.cluster_uid = "another-cluster"
    assert cli.Session(target, session.args).directory != session.directory


@pytest.mark.integration
def test_release_discovery_supports_all_states_without_removed_helm4_flag(session, monkeypatch):
    calls = []

    def helm(*args):
        calls.append(args)
        return result([])

    monkeypatch.setattr(session, "helm", helm)
    assert session.release_info() is None
    assert "--pending" in calls[0] and "--uninstalling" in calls[0]
    assert "-a" not in calls[0] and "--all" not in calls[0]


@pytest.mark.integration
def test_up_uses_helm_not_apply_and_reuses_owned_retained_pvc(session, monkeypatch):
    session.init()
    target = session.target
    target.objects["storageclass", "standard"] = {"provisioner": "k8s.io/minikube-hostpath"}
    target.objects["PersistentVolumeClaim", "review-history"] = {
        "kind": "PersistentVolumeClaim",
        "metadata": metadata("review-history", owned=True),
    }
    monkeypatch.setattr(session, "release_info", lambda: None)
    monkeypatch.setattr(session, "doctor", lambda *a: {"architecture": "arm64"})
    monkeypatch.setattr(
        session,
        "build",
        lambda a: {
            c: {"image": {"tag": "test", "digest": "", "pullPolicy": "Never"}}
            for c in ("backend", "frontend")
        },
    )
    monkeypatch.setattr(session, "evidence", lambda: {"revision": 1})
    calls = []
    monkeypatch.setattr(session, "helm", lambda *a, **kw: calls.append(a) or result())
    session.up()
    assert calls[0][:3] == ("upgrade", "--install", "local-review")
    assert "--reset-values" in calls[0] and "--reuse-values" not in calls[0]
    assert not any(a[0] == "apply" for a, _ in target.calls)
    assert session.read("values-local.json")["backend"]["image"]["pullPolicy"] == "Never"
    assert session.read("deployment.json") == {"revision": 1}


@pytest.mark.integration
@pytest.mark.security
def test_foreign_resource_blocks_up_before_image_build(session, monkeypatch):
    session.init()
    session.target.objects["Deployment", "review-backend"] = {
        "kind": "Deployment",
        "metadata": metadata("review-backend"),
    }
    monkeypatch.setattr(session, "build", lambda *_: pytest.fail("must not build"))
    with pytest.raises(ValueError, match="not owned"):
        session.up()


@pytest.mark.integration
def test_uninstall_retains_data_and_purge_only_deletes_owned_resources(session, monkeypatch):
    session.init()
    pvc = {"kind": "PersistentVolumeClaim", "metadata": metadata("review-history", owned=True)}
    external = {"kind": "PersistentVolumeClaim", "metadata": metadata("external")}
    session.target.objects["pvc", "review-history"] = pvc
    session.target.objects["pvc", "external"] = external
    monkeypatch.setattr(session, "release_info", lambda: None)
    lifecycle.undeploy(session)
    assert session.target.object("pvc", "review-history")
    session.args.purge_data = True
    removed = []

    def delete(s, kind, name, m):
        removed.append((kind, name))
        s.target.objects.pop(("pvc" if kind == "persistentvolumeclaims" else "secret", name))

    monkeypatch.setattr(lifecycle, "delete_exact", delete)
    lifecycle.undeploy(session)
    assert removed == [("persistentvolumeclaims", "review-history"), ("secrets", "review-secrets")]
    assert session.target.object("pvc", "external")


@pytest.mark.integration
def test_explicit_existing_claim_never_purged_even_with_helm_annotations(session, monkeypatch):
    session.init()
    session.target.objects["pvc", "review-history"] = {
        "kind": "PersistentVolumeClaim",
        "metadata": metadata("review-history", owned=True),
    }
    monkeypatch.setattr(session, "release_info", lambda: {"version": 1})
    monkeypatch.setattr(session, "validate_release", lambda _: None)
    values = session.options()
    values["persistence"]["history"]["existingClaim"] = "review-history"
    monkeypatch.setattr(
        session, "helm", lambda *a, **kw: result(values) if a[0] == "get" else result()
    )
    monkeypatch.setattr(
        lifecycle, "delete_exact", lambda s, k, n, m: s.target.objects.pop(("secret", n))
    )
    session.args.purge_data = True
    lifecycle.undeploy(session)
    assert session.target.object("pvc", "review-history")


@pytest.mark.integration
def test_namespace_deletion_blocked_by_unknown_resources(session, monkeypatch):
    session.init()
    session.args.delete_namespace = session.args.purge_data = True
    monkeypatch.setattr(session, "release_info", lambda: None)
    monkeypatch.setattr(
        lifecycle,
        "inventory",
        lambda _: [{"resource": "configmaps", "metadata": metadata("other-app")}],
    )
    with pytest.raises(ValueError, match="other-app"):
        lifecycle.undeploy(session)
    assert session.target.object("namespace", session.namespace)


@pytest.mark.integration
def test_storage_failure_is_reported_without_force_deletion(session, monkeypatch):
    session.init()
    session.args.purge_data = True
    session.args.delete_timeout = 0
    session.target.objects["pvc", "review-history"] = {
        "kind": "PersistentVolumeClaim",
        "metadata": metadata("review-history", owned=True),
    }
    session.target.pvs = [
        {
            "metadata": {"name": "stuck-pv"},
            "spec": {
                "claimRef": {"namespace": session.namespace, "uid": "uid-review-history"},
                "persistentVolumeReclaimPolicy": "Delete",
            },
            "status": {"phase": "Released"},
        }
    ]
    monkeypatch.setattr(session, "release_info", lambda: None)
    monkeypatch.setattr(lifecycle, "delete_exact", lambda *a: None)
    with pytest.raises(ValueError, match="storage cleanup incomplete"):
        lifecycle.undeploy(session)
    assert session.read("cleanup.json")["status"] == "storage_pending"
    assert not any(a[0] == "patch" for a, _ in session.target.calls)


@pytest.mark.integration
def test_quiesce_restores_frontend_when_idle_guard_rejects(session, monkeypatch):
    namespace(session)
    for component in ("backend", "frontend"):
        session.target.objects["deployment", "review-" + component] = {
            "kind": "Deployment",
            "metadata": metadata("review-" + component, owned=True),
            "spec": {"replicas": 1},
        }
    changes = []

    def scale(s, d, count):
        changes.append((d["metadata"]["name"], count))
        s.target.objects["deployment", d["metadata"]["name"]]["spec"]["replicas"] = count

    @contextlib.contextmanager
    def guard(*a, **kw):
        raise ValueError("active queue")
        yield

    monkeypatch.setattr(lifecycle, "scale", scale)
    monkeypatch.setattr(lifecycle, "wait_stopped", lambda *a: None)
    monkeypatch.setattr(lifecycle, "pods_for", lambda *a: [{"metadata": {"name": "backend-pod"}}])
    monkeypatch.setattr(lifecycle, "idle_guard", guard)
    with pytest.raises(ValueError, match="active queue"):
        with lifecycle.quiesced(session, restore=True):
            pytest.fail("must not mutate release")
    assert changes == [("review-frontend", 0), ("review-frontend", 1)]


@pytest.mark.integration
def test_rollback_is_explicit_and_uses_helm(session, monkeypatch):
    namespace(session)
    session.args.revision = 2
    monkeypatch.setattr(session, "release_info", lambda: {"version": 3})
    monkeypatch.setattr(session, "validate_release", lambda _: None)
    monkeypatch.setattr(session, "release_manifest", lambda _: [])
    monkeypatch.setattr(session, "preflight", lambda *a: None)
    monkeypatch.setattr(session, "evidence", lambda: {"revision": 4})
    calls = []

    def helm(*a, **kw):
        calls.append(a)
        return result([{"revision": 2}]) if a[0] == "history" else result({})

    monkeypatch.setattr(session, "helm", helm)
    session.rollback()
    assert ("rollback", "local-review", "2") == next(a for a in calls if a[0] == "rollback")[:3]


@pytest.mark.integration
@pytest.mark.parametrize(
    "args",
    [
        ["undeploy", "--purge-data"],
        ["up", "--delete-namespace"],
        ["rollback"],
        ["up", "--namespace", "kube-system"],
    ],
)
def test_destructive_and_target_validation_precedes_connection(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["minikube_helm.py", *args, "--profile", "minikube"])
    monkeypatch.setattr(cli, "connect", lambda *a: pytest.fail("must not connect"))
    with pytest.raises(ValueError):
        cli.main()


@pytest.mark.integration
def test_shell_routes_helm_help_without_cluster():
    output = subprocess.check_output([str(ROOT / "scripts/minikube_demo.sh"), "--help"], text=True)
    assert "--release" in output
    assert "legacy" not in output


@pytest.mark.integration
@pytest.mark.contract
def test_legacy_command_is_rejected_before_connection(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["minikube_helm.py", "legacy", "status", "--profile", "minikube"]
    )
    monkeypatch.setattr(cli, "connect", lambda *a: pytest.fail("must not connect"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.integration
@pytest.mark.security
def test_init_rejects_retired_namespace_owner_without_mutation(session):
    namespace(session)
    session.target.objects["namespace", session.namespace]["metadata"]["annotations"][
        "local-review-demo/owner"
    ] = "retired-owner"
    with pytest.raises(ValueError, match="foreign ownership"):
        session.init()
    assert not session.target.objects.get(("secret", "review-secrets"))


@pytest.mark.integration
@pytest.mark.security
def test_status_reads_manual_helm_revision_without_private_deployment_record(
    session, monkeypatch, capsys
):
    namespace(session)
    info = {
        "chart": {"metadata": {"name": "local-review"}},
        "version": 7,
        "info": {"status": "deployed"},
    }
    monkeypatch.setattr(session, "release_info", lambda: info)
    monkeypatch.setattr(session, "release_manifest", lambda: [])
    monkeypatch.setattr(session.target, "kubectl", lambda *a, **kw: result("pods ready"))
    session.status()
    assert '"revision": 7' in capsys.readouterr().out
    assert not session.read("deployment.json")
    session.save("verification.json", {"status": "passed", "evidence": {"revision": 6}})
    monkeypatch.setattr(session, "evidence", lambda: {"revision": 7})
    session.status()
    assert "stale" in capsys.readouterr().out


@pytest.mark.integration
def test_pending_release_can_be_inspected_but_not_mutated(session, monkeypatch):
    info = {"chart": {"metadata": {"name": "local-review"}}, "info": {"status": "pending-upgrade"}}
    monkeypatch.setattr(session, "release_manifest", lambda: [])
    session.validate_release(info, allow_pending=True)
    with pytest.raises(ValueError, match="pending"):
        session.validate_release(info)


@pytest.mark.integration
def test_namespace_cleanup_deletes_only_after_inventory_and_storage_checks(session, monkeypatch):
    session.init()
    session.args.delete_namespace = session.args.purge_data = True
    monkeypatch.setattr(session, "release_info", lambda: None)
    calls = []
    monkeypatch.setattr(lifecycle, "inventory", lambda _: calls.append("inventory") or [])

    def delete(s, kind, name, m):
        calls.append(kind)
        s.target.objects.pop(("namespace" if kind == "namespaces" else "secret", name))

    monkeypatch.setattr(lifecycle, "delete_exact", delete)
    lifecycle.undeploy(session)
    assert calls == ["inventory", "secrets", "inventory", "namespaces"]
    assert session.read("cleanup.json")["namespace_deleted"] is True


@pytest.mark.integration
@pytest.mark.requires_helm
def test_docker_limits_bound_overstated_node_capacity(session, monkeypatch):
    def kubectl(*args, **kw):
        if args[:2] == ("get", "nodes"):
            return result(
                {
                    "items": [
                        {
                            "status": {
                                "allocatable": {"cpu": "10", "memory": "16Gi"},
                                "conditions": [{"type": "Ready", "status": "True"}],
                                "nodeInfo": {"architecture": "arm64"},
                            }
                        }
                    ]
                }
            )
        return result({"items": []})

    monkeypatch.setattr(session.target, "kubectl", kubectl)
    session.target.objects["storageclass", "standard"] = {"provisioner": "k8s.io/minikube-hostpath"}

    def run(*args, **kw):
        if args[:2] == ("docker", "inspect"):
            return result([{"HostConfig": {"NanoCpus": 2_000_000_000, "Memory": 4 * 1024**3}}])
        return result()

    values = session.options()
    resources = session.render(values)
    monkeypatch.setattr(diagnostics, "run", run)
    report = session.doctor(values, resources)
    assert report["resources"]["cpu"]["capacity"] == 2
    assert report["resources"]["memory"]["capacity"] == 4 * 1024**3
    assert len(report["warnings"]) == 2


@pytest.mark.integration
def test_failed_up_journal_never_records_success(session, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "minikube_helm.py",
            "up",
            "--profile",
            "minikube",
            "--state-root",
            str(session.directory.parents[1]),
        ],
    )

    @contextlib.contextmanager
    def connect(*args):
        yield session.target

    monkeypatch.setattr(cli, "connect", connect)

    def fail(self):
        raise RuntimeError("Helm upgrade failed")

    monkeypatch.setattr(cli.Session, "up", fail)
    with pytest.raises(RuntimeError, match="Helm upgrade failed"):
        cli.main()
    assert session.read("operation.json")["status"] == "failed"
    assert session.read("verification.json")["status"] == "stale"


@pytest.mark.integration
def test_pvc_delete_sends_uid_precondition(session):
    namespace(session)
    calls = []
    original = session.target.kubectl

    def kubectl(*args, **kwargs):
        if args[0] in {"delete", "wait"}:
            calls.append((args, kwargs))
            return result()
        return original(*args, **kwargs)

    session.target.kubectl = kubectl
    lifecycle.delete_exact(
        session, "persistentvolumeclaims", "review-history", metadata("review-history")
    )
    options = json.loads(calls[0][1]["data"])
    assert options["preconditions"] == {"uid": "uid-review-history", "resourceVersion": "1"}
    assert "--raw" in calls[0][0]


@pytest.mark.integration
@pytest.mark.recovery
@pytest.mark.parametrize(
    "outcome,skip,expected",
    [
        ("completed", False, "passed"),
        ("completed", True, "partial"),
        ("failed", False, "failed"),
    ],
)
def test_helm_acceptance_reports_real_review_and_persistence_separately(
    session, monkeypatch, outcome, skip, expected
):
    import deployment.helm.verify as verify

    session.args.skip_restart = skip
    monkeypatch.setattr(session, "wait_ready", lambda: None)
    monkeypatch.setattr(session, "evidence", lambda: {"revision": 3, "images": ["sha256:test"]})
    monkeypatch.setattr(session, "origin", lambda: ("http://localhost:8080", 8080))
    monkeypatch.setattr(session, "forward", lambda _: contextlib.nullcontext())
    monkeypatch.setattr(
        session,
        "config",
        lambda: {
            "OLLAMA_MODEL": "qwen3:1.7b",
            "OLLAMA_MODEL_DIGEST": "sha256:test",
        },
    )
    monkeypatch.setattr(
        verify, "cache_inventory", lambda _: {"qwen3:1.7b": ["sha256:" + "a" * 64, 123]}
    )
    monkeypatch.setattr(verify, "login", lambda *a: None)
    monkeypatch.setattr(verify, "completed_review", lambda *a: None)
    monkeypatch.setattr(verify, "history_contains", lambda *a: {"review_result": "same result"})
    restarts = []
    monkeypatch.setattr(verify, "restart", lambda _: restarts.append(True))

    class Client:
        def __init__(self, **kwargs):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path):
            return SimpleNamespace(
                status_code=200,
                text="<html>",
                headers={
                    h: "present"
                    for h in (
                        "content-security-policy",
                        "x-frame-options",
                        "x-content-type-options",
                        "referrer-policy",
                    )
                },
            )

    monkeypatch.setattr(verify.httpx, "Client", Client)

    def request(client, method, path, code, **kwargs):
        if code == 201:
            return {"csrf_token": "test"}
        if code == 202:
            return {"review_id": "test-review"}
        if path == "/api/v1/reviews/test-review" and code == 200:
            return {"status": outcome, "review_result": "same result"}
        return {"items": []}

    monkeypatch.setattr(verify, "request", request)
    if expected == "passed":
        verify.verify(session)
    else:
        with pytest.raises(ValueError):
            verify.verify(session)
    report = session.read("verification.json")
    assert report["status"] == expected
    assert report["api_acceptance_passed"] is (expected == "passed")
    assert bool(restarts) is (expected == "passed")
    assert report["ui_verified"] is False


@pytest.mark.integration
def test_unmanaged_workloads_are_not_reported_as_uninstalled(session, monkeypatch):
    session.init()
    session.target.objects["deployment", "review-dynamodb"] = {
        "metadata": metadata("review-dynamodb")
    }
    monkeypatch.setattr(session, "release_info", lambda: None)
    with pytest.raises(ValueError, match="Workloads exist without a Helm release"):
        lifecycle.undeploy(session)


@pytest.mark.integration
def test_successful_purge_allows_a_new_installation_to_be_purged(session, monkeypatch):
    session.init()
    session.save(
        "cleanup.json",
        {
            "status": "purged",
            "claims": {"review-history": metadata("review-history", owned=True, uid="old-pvc")},
        },
    )
    session.target.objects["pvc", "review-history"] = {
        "kind": "PersistentVolumeClaim",
        "metadata": metadata("review-history", owned=True, uid="new-pvc"),
    }
    session.args.purge_data = True
    monkeypatch.setattr(session, "release_info", lambda: None)
    removed = []
    monkeypatch.setattr(lifecycle, "delete_exact", lambda s, k, n, m: removed.append(m["uid"]))
    lifecycle.undeploy(session)
    assert "new-pvc" in removed


@pytest.mark.integration
def test_namespace_cleanup_reports_old_untracked_released_pv(session, monkeypatch):
    session.init()
    session.args.purge_data = session.args.delete_namespace = True
    session.args.delete_timeout = 0
    session.target.pvs = [
        {
            "metadata": {"name": "old-released-pv"},
            "spec": {
                "claimRef": {"namespace": session.namespace, "uid": "old-claim"},
                "persistentVolumeReclaimPolicy": "Delete",
            },
            "status": {"phase": "Released"},
        }
    ]
    monkeypatch.setattr(session, "release_info", lambda: None)
    monkeypatch.setattr(lifecycle, "inventory", lambda _: [])
    monkeypatch.setattr(lifecycle, "delete_exact", lambda *a: None)
    with pytest.raises(ValueError, match="old-released-pv"):
        lifecycle.undeploy(session)
    assert session.target.object("namespace", session.namespace)
    assert session.read("cleanup.json")["status"] == "storage_pending"
