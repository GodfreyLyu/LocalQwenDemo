"""Offline regression tests; these are never evidence of real-model acceptance."""

import contextlib
import json
from types import SimpleNamespace

import deployment.legacy.runtime as demo  # noqa: E402
import deployment.legacy.target as target  # noqa: E402
import pytest
from deployment.legacy.context import using_context


@pytest.fixture(scope="module")
def resources():
    """Render real Kustomize files offline; kubectl does not contact a cluster here."""
    return demo.render(8080)


@pytest.fixture
def diagnostic_environment(monkeypatch, resources, tmp_path):
    config = {"issue": None, "owned": False, "metrics": True}
    monkeypatch.setattr(demo, "guard_target", lambda: None)
    monkeypatch.setattr(demo, "STATE", tmp_path / "target-state")
    monkeypatch.setattr(
        demo,
        "TARGET",
        {
            "node_name": "test-node",
            "cluster_uid": "test-uid",
            "minikube_home": str(tmp_path / ".minikube"),
        },
    )
    monkeypatch.setattr(target.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(demo.os, "environ", dict(demo.os.environ))
    monkeypatch.setattr(demo, "require_local_docker", lambda: None)
    monkeypatch.setattr(demo, "state", lambda **kw: {"owner": "mine"} if config["owned"] else None)
    monkeypatch.setattr(demo, "check_ownership", lambda _: None)

    @contextlib.contextmanager
    def connected(_):
        yield {}

    monkeypatch.setattr(target, "connected_target", connected)

    def unavailable():
        raise demo.DemoError("Measurement command unavailable")

    monkeypatch.setattr(
        demo,
        "host_memory",
        lambda: (
            unavailable()
            if config["issue"] == "missing_host_memory"
            else (32 * demo.GIB, (1 if config["issue"] == "host_memory" else 20) * demo.GIB)
        ),
    )
    monkeypatch.setattr(
        target.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=(1 if config["issue"] == "host_disk" else 40) * demo.GIB),
    )
    monkeypatch.setattr(demo, "render", lambda _: resources)
    monkeypatch.setattr(
        demo,
        "obj",
        lambda kind, *a, **kw: (
            {"provisioner": config.get("provisioner", "k8s.io/minikube-hostpath")}
            if kind == "storageclass"
            else None
        ),
    )
    monkeypatch.setattr(target, "cni_status", lambda: {"policy_support": "not_enforced"})
    monkeypatch.setattr(
        target,
        "workload_inventory",
        lambda: unavailable() if config["issue"] == "missing_workload_inventory" else [],
    )
    monkeypatch.setattr(
        target, "owned_pod_names", lambda *a: {"owned-pod"} if config["owned"] else set()
    )
    monkeypatch.setattr(
        target,
        "memory_usage",
        lambda: {(demo.NAMESPACE, "owned-pod"): 0} if config["metrics"] else None,
    )
    monkeypatch.setattr(target, "source_fingerprint", lambda _: "fingerprint")
    monkeypatch.setattr(target, "reusable_images", lambda *a: {})

    def kubernetes(*args, **kwargs):
        issue = config["issue"]
        if args[0] == "version":
            if issue == "missing_version":
                return unavailable()
            data = {key: {"gitVersion": "v1.34.0"} for key in ("clientVersion", "serverVersion")}
            if issue == "version_skew":
                data["clientVersion"]["gitVersion"] = "v1.36.0"
        elif args[:2] == ("get", "nodes"):
            data = {
                "items": [
                    {
                        "spec": {
                            "unschedulable": issue == "not_ready",
                            "taints": [{"effect": "NoSchedule"}] if issue == "taint" else [],
                        },
                        "status": {
                            "nodeInfo": {"architecture": config.get("node_arch", "arm64")},
                            "conditions": [{"type": "Ready", "status": "True"}],
                            "allocatable": {}
                            if issue == "missing_node_allocatable"
                            else {"cpu": "10", "memory": "24Gi"},
                        },
                    }
                ]
            }
        else:
            assert args[:2] == ("exec", "owned-pod")
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(stdout=json.dumps(data))

    def docker(args):
        assert args[0] == "docker"
        issue = config["issue"]
        if args[1] == "info":
            data = {
                "OSType": "linux",
                "Architecture": config.get("docker_arch", "arm64"),
                "MemTotal": (2 if issue == "docker_quota" else 24) * demo.GIB,
                "NCPU": 10,
            }
        elif args[1] == "inspect":
            if issue == "missing_node_limits":
                return unavailable()
            data = {
                "memory": (3 if issue == "node_capacity" else 24) * demo.GIB,
                "nano": 2_000_000_000 if issue == "node_capacity" else 0,
                "quota": 0,
                "period": 0,
                "cpuset": "",
            }
        elif args[1] == "stats":
            if issue == "missing_docker_stats":
                return unavailable()
            return SimpleNamespace(
                stdout="invalid"
                if issue == "malformed_docker_stats"
                else "test-node\t1GiB / 24GiB\t1%\n"
            )
        else:
            assert args[1:4] == ["exec", "test-node", "df"]
            if issue == "missing_vm_disk":
                return unavailable()
            free = 1024 if issue == "vm_disk" else 41943040
            return SimpleNamespace(
                stdout="Filesystem 1024-blocks Used Available Capacity Mounted\n"
                f"/dev/test 52428800 10485760 {free} 20% /var/lib\n"
            )
        return SimpleNamespace(stdout=json.dumps(data))

    monkeypatch.setattr(demo, "k", kubernetes)
    monkeypatch.setattr(demo, "run", docker)
    return config


@pytest.fixture(autouse=True)
def isolated_minikube_state_root(monkeypatch, tmp_path):
    """Every deployment test uses private state, regardless of its filename."""
    monkeypatch.setenv("LOCAL_QWEN_STATE_HOME", str(tmp_path))
    with using_context(demo):
        yield
