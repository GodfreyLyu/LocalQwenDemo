"""Shared state/import exercises use temporary directories and a simulated read-only target."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minikube_demo as d  # noqa: E402
import minikube_store as store  # noqa: E402
from minikube_target import target_path  # noqa: E402


@pytest.fixture
def legacy(monkeypatch, tmp_path):
    root = tmp_path / "shared"
    home = tmp_path / ".minikube"
    destination = target_path(root, home, "minikube", "cluster-one")
    source = tmp_path / "old" / ".local/minikube-demo"
    source = target_path(source, home, "minikube", "cluster-one")
    source.mkdir(parents=True)
    owner = dict(
        root=str(tmp_path / "old"),
        profile="minikube",
        minikube_home=str(home),
        cluster_uid="cluster-one",
        namespace_uid="namespace-one",
        owner="a" * 48,
    )
    (source / "owner.json").write_text(json.dumps(owner))
    (source / "deployment.json").write_text(
        json.dumps(
            owner
            | {
                "status": "failed",
                "application_ready": False,
                "build_fingerprints": {"review-backend": "old-proof"},
            }
        )
    )
    (source / "verification.json").write_text(
        json.dumps({"status": "failed", "review_completed": True, "persistence_verified": False})
    )
    (source / "acceptance-account.json").write_text('{"password":"offline-private-fixture"}')
    monkeypatch.setattr(d, "ROOT", tmp_path / "new")
    monkeypatch.setattr(d, "STATE_ROOT", root)
    monkeypatch.setattr(d, "STATE", destination)
    monkeypatch.setattr(d, "PROFILE", "minikube")
    monkeypatch.setattr(
        d,
        "TARGET",
        {
            k: v
            for k, v in (owner | {"root": str(d.ROOT)}).items()
            if k not in {"owner", "namespace_uid"}
        },
    )
    monkeypatch.setattr(d, "guard_target", lambda: None)
    monkeypatch.setattr(d, "render", lambda *a: [])
    namespace = {
        "kind": "Namespace",
        "metadata": {
            "uid": owner["namespace_uid"],
            "name": d.NAMESPACE,
            "annotations": {d.OWNER_KEY: owner["owner"]},
        },
    }
    monkeypatch.setattr(d, "obj", lambda kind, *a, **kw: namespace if kind == "namespace" else None)
    return SimpleNamespace(source=source, destination=destination, owner=owner, namespace=namespace)


def test_import_keeps_original_evidence_private_and_repeats(legacy, capsys):
    original = {p.name: p.read_bytes() for p in legacy.source.iterdir()}
    with store.operation_lock():
        store.import_state(legacy.source)
    for name, content in original.items():
        assert (legacy.source / name).read_bytes() == content
        assert (legacy.destination / name).read_bytes() == content
        assert (legacy.destination / name).stat().st_mode & 0o777 == 0o600
    assert legacy.destination.stat().st_mode & 0o777 == 0o700
    assert d.state()["root"] != d.TARGET["root"]
    # Later shared reports are authoritative; a repeated import cannot revert them.
    d.save("verification.json", {"status": "not_run"})
    with store.operation_lock():
        store.import_state(legacy.source)
    assert json.loads((d.STATE / "verification.json").read_text()) == {"status": "not_run"}
    assert "offline-private-fixture" not in capsys.readouterr().out


def test_import_interruption_before_publication_is_retryable(legacy, monkeypatch):
    rename = Path.rename
    with monkeypatch.context() as scoped:

        def interrupted(path, target):
            if path.name.startswith(".import-"):
                raise KeyboardInterrupt
            return rename(path, target)

        scoped.setattr(Path, "rename", interrupted)
        with store.operation_lock(), pytest.raises(KeyboardInterrupt):
            store.import_state(legacy.source)
    assert not d.STATE.exists()
    assert (legacy.source / "owner.json").is_file()
    with store.operation_lock():
        store.import_state(legacy.source)
    assert d.state()["owner"] == legacy.owner["owner"]


@pytest.mark.parametrize(
    "conflict", ["target", "namespace_uid", "resource_owner", "record", "symlink", "missing_uid"]
)
def test_import_refuses_untrusted_or_conflicting_evidence(legacy, conflict):
    if conflict == "target":
        d.STATE.mkdir(parents=True)
        (d.STATE / "owner.json").write_text('{"owner":"another"}')
    elif conflict == "namespace_uid":
        legacy.namespace["metadata"]["uid"] = "replacement"
    elif conflict == "resource_owner":
        legacy.namespace["metadata"]["annotations"][d.OWNER_KEY] = "another"
    elif conflict == "record":
        (legacy.source / "deployment.json").write_text('{"cluster_uid":"different"}')
    elif conflict == "missing_uid":
        legacy.owner.pop("namespace_uid")
        (legacy.source / "owner.json").write_text(json.dumps(legacy.owner))
    else:
        (legacy.source / "linked").symlink_to(legacy.source / "owner.json")
    with store.operation_lock(), pytest.raises(d.DemoError):
        store.import_state(legacy.source)
    assert not (d.STATE / "migration.json").exists()


def test_automatic_import_only_checks_current_checkout(legacy, monkeypatch):
    monkeypatch.setattr(d, "ROOT", Path(legacy.owner["root"]))
    with store.operation_lock():
        store.maybe_import(SimpleNamespace(from_state=None, command="up"))
    assert d.state()["owner"] == legacy.owner["owner"]


def test_changed_cluster_uses_new_target_and_keeps_history(legacy, monkeypatch):
    with store.operation_lock():
        store.import_state(legacy.source)
    old = d.STATE
    monkeypatch.setattr(
        d,
        "STATE",
        target_path(d.STATE_ROOT, Path(d.TARGET["minikube_home"]), d.PROFILE, "cluster-two"),
    )
    monkeypatch.setattr(d, "TARGET", d.TARGET | {"cluster_uid": "cluster-two"})
    assert d.state(optional=True) is None
    assert (old / "owner.json").is_file()
    with store.operation_lock(), pytest.raises(d.DemoError, match="target identity"):
        store.import_state(legacy.source)


def test_shared_operation_lock_excludes_another_checkout_process(legacy):
    code = """import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import minikube_demo as d
import minikube_store as s
d.STATE_ROOT, d.STATE, d.ROOT = map(Path, sys.argv[2:])
try:
    with s.operation_lock(): pass
except d.DemoError:
    sys.exit(23)
"""
    args = [
        sys.executable,
        "-c",
        code,
        str(Path(__file__).resolve().parents[1]),
        str(d.STATE_ROOT),
        str(d.STATE),
        "/another/checkout",
    ]
    with store.operation_lock():
        assert subprocess.run(args, capture_output=True, timeout=10).returncode == 23
    assert subprocess.run(args, capture_output=True, timeout=10).returncode == 0


def test_explicit_override_and_xdg_default(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCAL_QWEN_STATE_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert store.state_root() == tmp_path / "xdg/local-qwen-demo"
    monkeypatch.setenv("LOCAL_QWEN_STATE_HOME", str(tmp_path / "env"))
    assert store.state_root() == tmp_path / "env"
    assert store.state_root(str(tmp_path / "explicit")) == tmp_path / "explicit"
    assert not (tmp_path / "explicit").exists()


def test_missing_namespace_recovery_archives_instead_of_claiming_success(legacy, monkeypatch):
    with store.operation_lock():
        store.import_state(legacy.source)
    monkeypatch.setattr(d, "obj", lambda *a, **kw: None)
    previous = (d.STATE / "owner.json").read_bytes()
    store.recover_absent_namespace()
    assert not d.state().get("namespace_uid")
    assert d.state()["owner"] != legacy.owner["owner"]
    assert any(p.read_bytes() == previous for p in (d.STATE / "attempts").glob("*/owner.json"))
    assert not (d.STATE / "deployment.json").exists()
    assert json.loads((d.STATE / "recovery.json").read_text())["application_ready"] is False


def test_shared_target_conflict_after_completed_import_is_not_merged(legacy):
    with store.operation_lock():
        store.import_state(legacy.source)
    d.save("owner.json", legacy.owner | {"owner": "b" * 48})
    with store.operation_lock(), pytest.raises(d.DemoError, match="conflict"):
        store.import_state(legacy.source)
    assert d.state()["owner"] == "b" * 48


def test_absent_namespace_preserves_local_legacy_without_automatic_adoption(legacy, monkeypatch):
    monkeypatch.setattr(d, "ROOT", Path(legacy.owner["root"]))
    monkeypatch.setattr(d, "obj", lambda *a, **kw: None)
    with store.operation_lock():
        store.maybe_import(SimpleNamespace(from_state=None, command="up"))
    assert not d.STATE.exists()
    assert (legacy.source / "owner.json").exists()


def test_import_respects_the_old_checkout_operation_lock(legacy):
    with store.file_lock(legacy.source / "operation.lock"):
        with store.operation_lock(), pytest.raises(d.DemoError, match="operation is active"):
            store.import_state(legacy.source)
    assert not d.STATE.exists()
