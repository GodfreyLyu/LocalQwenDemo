"""Offline regression tests; these are never evidence of real-model acceptance."""

import base64
import contextlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import minikube_demo as demo  # noqa: E402
import minikube_state as state_records  # noqa: E402
import minikube_target as target  # noqa: E402
import minikube_verify as acceptance  # noqa: E402


@pytest.fixture(scope="module")
def resources():
    """Render real Kustomize files offline; kubectl does not contact a cluster here."""
    return demo.render(8080)


def resource(resources, kind, name):
    return next(r for r in resources if r["kind"] == kind and r["metadata"]["name"] == name)


@pytest.mark.parametrize("render_path", ["overlay", "deployment"])
def test_openmp_setting_is_backend_only_and_preserves_inference_contract(render_path):
    if render_path == "overlay":
        rendered = list(
            yaml.safe_load_all(
                subprocess.run(
                    ["kubectl", "kustomize", str(ROOT / "deploy/kustomize/overlays/minikube")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
        )
    else:
        rendered = demo.render(8080)
    cfg = resource(rendered, "ConfigMap", "review-config")["data"]
    pod = resource(rendered, "Deployment", "review-backend")["spec"]["template"]["spec"]
    backend = next(c for c in pod["containers"] if c["name"] == "review-backend")
    assert backend["env"] == [{"name": "OMP_NUM_THREADS", "value": "2"}]
    assert backend["envFrom"] == [
        {"configMapRef": {"name": "review-config"}},
        {"secretRef": {"name": "review-secrets"}},
    ]
    assert (
        cfg.items()
        >= {
            "MODEL_ID": "Qwen/Qwen3-1.7B",
            "MODEL_REVISION": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
            "MODEL_DTYPE": "bfloat16",
            "MODEL_CPU_THREADS": "2",
            "MODEL_MAX_INPUT_TOKENS": "2048",
            "MODEL_MAX_OUTPUT_TOKENS": "384",
            "MODEL_INFERENCE_CONCURRENCY": "1",
            "INFERENCE_TIMEOUT_SECONDS": "300",
        }.items()
    )
    assert backend["resources"] == {
        "requests": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "256Mi"},
        "limits": {"cpu": "2", "memory": "6Gi", "ephemeral-storage": "1Gi"},
    }
    assert "OMP_NUM_THREADS" not in cfg
    for deployment in (r for r in rendered if r["kind"] == "Deployment"):
        spec = deployment["spec"]["template"]["spec"]
        for container in spec["containers"] + spec.get("initContainers", []):
            if container is not backend:
                assert all(e["name"] != "OMP_NUM_THREADS" for e in container.get("env", []))


def test_overlay_preserves_product_contract_and_has_no_cloud_resources(resources):
    cfg = resource(resources, "ConfigMap", "review-config")["data"]
    assert (
        cfg.items()
        >= {
            "ENVIRONMENT": "local",
            "COOKIE_SECURE": "false",
            "ALLOWED_ORIGIN": "http://localhost:8080",
            "DYNAMODB_ENDPOINT_URL": "http://review-dynamodb:8000",
            "DYNAMODB_TABLE": "llm-review-users",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": "local",
            "AWS_SECRET_ACCESS_KEY": "local",
            "MODEL_ID": acceptance.MODEL,
            "MODEL_REVISION": acceptance.REVISION,
            "MODEL_DTYPE": "bfloat16",
            "MODEL_CPU_THREADS": "2",
            "MODEL_MAX_INPUT_TOKENS": "2048",
            "MODEL_MAX_OUTPUT_TOKENS": "384",
            "INFERENCE_TIMEOUT_SECONDS": "300",
            "MODEL_INFERENCE_CONCURRENCY": "1",
        }.items()
    )
    allowed = {
        "ConfigMap",
        "ServiceAccount",
        "Deployment",
        "Service",
        "NetworkPolicy",
        "PersistentVolumeClaim",
    }
    assert all(r["kind"] in allowed for r in resources)
    assert all(r["metadata"]["namespace"] == demo.NAMESPACE for r in resources)
    serialized = json.dumps(resources)
    for forbidden in ("gp3", "ebs.csi", "169.254.170.23", "10.74.0.0", "arn:aws:", "REPLACE_"):
        assert forbidden not in serialized
    claims = [r for r in resources if r["kind"] == "PersistentVolumeClaim"]
    assert {r["metadata"]["name"] for r in claims} == {
        "review-model-cache",
        "review-history",
        "review-dynamodb",
    }
    assert all(r["spec"]["storageClassName"] == "standard" for r in claims)


def test_all_application_containers_remain_hardened_and_single_replica(resources):
    for deployment in (r for r in resources if r["kind"] == "Deployment"):
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"]["type"] == "Recreate"
        pod = deployment["spec"]["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"]
        assert pod["automountServiceAccountToken"] is False
        for container in pod["containers"]:
            assert container["securityContext"]["readOnlyRootFilesystem"]
            assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
            assert not container["securityContext"]["allowPrivilegeEscalation"]
            assert all(
                probe in container for probe in ("startupProbe", "livenessProbe", "readinessProbe")
            )
    backend = resource(resources, "Deployment", "review-backend")["spec"]["template"]["spec"]
    assert backend["securityContext"]["runAsUser"] == 10001
    assert backend["containers"][0]["resources"]["limits"]["memory"] == "6Gi"
    for init in backend["initContainers"]:
        assert init["securityContext"]["capabilities"]["add"] == ["CHOWN", "FOWNER"]
        assert "os.chown" in init["args"][0] and "rmtree" not in init["args"][0]


def permission_bits(metadata, uid, groups):
    """Evaluate ordinary non-root Unix DAC against recorded image/volume metadata."""
    assert uid != 0
    shift = 6 if uid == metadata["uid"] else 3 if metadata["gid"] in groups else 0
    return (int(metadata["mode"], 8) >> shift) & 7


def test_dynamodb_effective_identity_can_traverse_the_actual_image_startup_path(resources):
    image = json.loads(
        (ROOT / "scripts/tests/fixtures/dynamodb-local-3.1.0-arm64.json").read_text()
    )
    pod = resource(resources, "Deployment", "review-dynamodb")["spec"]["template"]["spec"]
    container = pod["containers"][0]
    security = pod["securityContext"] | container["securityContext"]
    uid = security["runAsUser"]
    groups = {security["runAsGroup"], pod["securityContext"]["fsGroup"]}
    assert container["image"] == image["image"]
    assert uid == image["uid"] and groups == {10001}
    command = container.get("command", image["entrypoint"]) + container.get("args", image["cmd"])
    assert command[:2] == ["java", "-jar"]
    jar = PurePosixPath(container.get("workingDir", image["working_dir"])) / command[2]

    def can_open(candidate_uid):
        return all(
            permission_bits(image["paths"][str(parent)], candidate_uid, groups) & 1
            for parent in jar.parents
        ) and bool(permission_bits(image["paths"][str(jar)], candidate_uid, groups) & 4)

    assert can_open(uid)
    # Regression: the JAR is readable, but the old UID cannot traverse its 0700 parent.
    # An absolute JAR path still needs exactly the same directory traversal permissions.
    assert permission_bits(image["paths"][str(jar)], 10001, groups) & 4
    assert not can_open(10001)
    for mount in container["volumeMounts"]:
        path = PurePosixPath(mount["mountPath"])
        assert path != jar and path not in jar.parents
    assert command[command.index("-dbPath") + 1] == "/data"
    assert "-sharedDb" in command and "-disableTelemetry" in command
    assert security["runAsNonRoot"] and security["readOnlyRootFilesystem"]
    assert not security["allowPrivilegeEscalation"]
    assert security["capabilities"] == {"drop": ["ALL"]}


def test_dynamodb_permission_init_is_idempotent_and_changes_only_volume_root(resources):
    pod = resource(resources, "Deployment", "review-dynamodb")["spec"]["template"]["spec"]
    init = pod["initContainers"][0]
    assert init["command"] == ["python", "-c"]
    assert init["securityContext"]["capabilities"] == {"drop": ["ALL"], "add": ["CHOWN", "FOWNER"]}
    assert not init["securityContext"]["allowPrivilegeEscalation"]
    assert init["securityContext"]["readOnlyRootFilesystem"]
    assert init["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]
    volume = {
        "/data": {"uid": 10001, "gid": 10001, "mode": "0770"},
        "/data/existing.db": {"uid": 10001, "gid": 10001, "mode": "0600"},
    }
    original_file = dict(volume["/data/existing.db"])

    def chown(path, uid, gid):
        assert path == "/data", "Existing database files must never be chowned recursively."
        volume[path].update(uid=uid, gid=gid)

    def chmod(path, mode):
        assert path == "/data", "Existing database files must never be chmodded recursively."
        volume[path]["mode"] = oct(mode)

    def import_os(name, *args):
        assert name == "os"
        return SimpleNamespace(chown=chown, chmod=chmod)

    for _ in range(2):
        # Execute the actual rendered init program with an instrumented filesystem boundary.
        exec(
            compile(init["args"][0], "volume-permissions", "exec"),
            {"__builtins__": {"__import__": import_os}},
        )
        uid = pod["securityContext"]["runAsUser"]
        groups = {pod["securityContext"]["runAsGroup"], pod["securityContext"]["fsGroup"]}
        assert permission_bits(volume["/data"], uid, groups) == 7
        assert volume["/data"]["uid"] == uid
        assert volume["/data/existing.db"] == original_file
    data = next(v for v in pod["volumes"] if v["name"] == "data")
    assert data["persistentVolumeClaim"]["claimName"] == "review-dynamodb"


def test_nginx_preserves_paths_origin_cookie_and_headers(resources):
    nginx = next(
        r["data"]["nginx.conf"]
        for r in resources
        if r["kind"] == "ConfigMap" and "nginx.conf" in r.get("data", {})
    )
    prod = (ROOT / "frontend/nginx.conf").read_text()
    assert "location /api/ { return 404; }" in prod
    for route in ("/api/", "/health/"):
        assert f"location {route} {{\n      proxy_pass http://review-backend:8000;" in nginx
    assert "proxy_pass http://review-backend:8000/" not in nginx
    for header in ("Origin $http_origin", "Cookie $http_cookie", "Host $http_host"):
        assert "proxy_set_header " + header in nginx
    for line in prod.splitlines():
        if "add_header" in line:
            assert line in nginx


def test_network_policy_allows_only_local_dependencies(resources):
    back = resource(resources, "NetworkPolicy", "review-backend")["spec"]
    front = resource(resources, "NetworkPolicy", "review-frontend")["spec"]
    db = resource(resources, "NetworkPolicy", "review-dynamodb")["spec"]
    assert back["ingress"][0]["from"] == [
        {"podSelector": {"matchLabels": {"app": "review-frontend"}}}
    ]
    assert back["egress"][2]["to"] == [{"podSelector": {"matchLabels": {"app": "review-dynamodb"}}}]
    assert front["ingress"] == []
    assert front["egress"][0]["to"] == [{"podSelector": {"matchLabels": {"app": "review-backend"}}}]
    assert db["egress"] == []
    assert back["egress"][1]["ports"] == [{"port": 443, "protocol": "TCP"}]


def test_port_override_and_unique_loaded_images():
    images = {
        "review-backend": "review-backend:unique-123",
        "review-frontend": "review-frontend:unique-123",
    }
    rendered = demo.render(8099, images)
    assert (
        resource(rendered, "ConfigMap", "review-config")["data"]["ALLOWED_ORIGIN"]
        == "http://localhost:8099"
    )
    for deployment in (r for r in rendered if r["kind"] == "Deployment"):
        pod = deployment["spec"]["template"]["spec"]
        for container in pod.get("initContainers", []) + pod["containers"]:
            if container["image"].startswith("review-"):
                assert container["image"] in images.values()
                assert container["imagePullPolicy"] == "Never"


def roomy_budget():
    return dict(
        desired={
            "cpu_request": 2.2,
            "memory_request": 4.5 * demo.GIB,
            "memory_limit": 7 * demo.GIB,
        },
        other={"cpu_request": 0.5, "memory_request": demo.GIB, "memory_limit": demo.GIB},
        allocatable={"cpu": 6, "memory": 10 * demo.GIB},
        host_available=10 * demo.GIB,
        docker_total=16 * demo.GIB,
        docker_used=2 * demo.GIB,
        node_used=demo.GIB,
        node_cap=10 * demo.GIB,
        own_used=0,
        disk_free=40 * demo.GIB,
        disk_need=28 * demo.GIB,
    )


def test_resource_budget_rejects_realistic_low_disk_and_memory():
    args = roomy_budget()
    assert not target.capacity_errors(**args)
    args.update(
        host_available=2 * demo.GIB,
        docker_total=8 * demo.GIB,
        node_cap=4 * demo.GIB,
        disk_free=12 * demo.GIB,
    )
    errors = target.capacity_errors(**args)
    assert any("Host memory pressure" in e for e in errors)
    assert any("Insufficient Docker quota" in e for e in errors)
    assert any("target node" in e for e in errors)
    assert any("Insufficient host disk space" in e for e in errors)


def test_repeat_budget_counts_only_incremental_memory():
    args = roomy_budget()
    args.update(
        own_used=7 * demo.GIB,
        host_available=2 * demo.GIB,
        node_used=8 * demo.GIB,
        docker_used=8 * demo.GIB,
    )
    assert not target.capacity_errors(**args)
    args["own_used"] = None
    assert any("not measured" in e for e in target.capacity_errors(**args))


def test_every_kubectl_operation_has_private_context_and_namespace(monkeypatch):
    called = Mock(return_value=SimpleNamespace(stdout=""))
    monkeypatch.setattr(demo, "run", called)
    demo.k("get", "pods")
    command = called.call_args.args[0]
    assert command[:7] == [
        "kubectl",
        "--kubeconfig",
        str(demo.KUBECONFIG_FILE),
        "--context",
        demo.PROFILE,
        "--namespace",
        demo.NAMESPACE,
    ]
    assert demo.clean_env()["MINIKUBE_HOME"].endswith("/.minikube")


def test_host_aws_profiles_and_hub_tokens_are_not_inherited(monkeypatch):
    for key in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "HF_TOKEN", "MINIKUBE_DRIVER"):
        monkeypatch.setenv(key, "do-not-use")
        assert key not in demo.clean_env()
    assert demo.clean_env()["AWS_CONFIG_FILE"] == "/dev/null"
    assert demo.clean_env()["AWS_EC2_METADATA_DISABLED"] == "true"


def test_resource_conflict_never_applies(monkeypatch):
    previous = {"kind": "Service", "metadata": {"name": "review-dynamodb"}}
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: previous)
    mutate = Mock()
    monkeypatch.setattr(demo, "k", mutate)
    with pytest.raises(demo.DemoError, match="Ownership conflict"):
        demo.apply([{"kind": "Service", "metadata": {"name": "review-dynamodb"}}], "mine")
    mutate.assert_not_called()


@pytest.mark.parametrize("secret", [None, "", "short"])
def test_existing_invalid_secret_fails_without_rotation(monkeypatch, secret):
    previous = {
        "kind": "Secret",
        "metadata": {"name": "review-secrets", "annotations": {demo.OWNER_KEY: "mine"}},
        "data": {}
        if secret is None
        else {"SIGNING_SECRET": base64.b64encode(secret.encode()).decode()},
    }
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: previous)
    mutate = Mock(return_value=SimpleNamespace(stdout=str(len(secret or ""))))
    monkeypatch.setattr(demo, "k", mutate)
    with pytest.raises(demo.DemoError, match="refusing rotation"):
        demo.ensure_secret("mine")
    assert all(call.args[0] == "get" for call in mutate.call_args_list)


def test_secret_is_reused_and_new_secret_uses_stdin_only(monkeypatch, capsys):
    previous = {
        "kind": "Secret",
        "metadata": {"name": "review-secrets", "annotations": {demo.OWNER_KEY: "mine"}},
        "data": {"SIGNING_SECRET": base64.b64encode(b"x" * 48).decode()},
    }
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: previous)
    mutate = Mock(return_value=SimpleNamespace(stdout="48"))
    monkeypatch.setattr(demo, "k", mutate)
    demo.ensure_secret("mine")
    assert all(call.args[0] == "get" for call in mutate.call_args_list)
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: None)
    demo.ensure_secret("mine")
    assert mutate.call_args.args == ("create", "-f", "-")
    created = json.loads(mutate.call_args.kwargs["data"])
    assert len(created["stringData"]["SIGNING_SECRET"]) >= 48
    assert capsys.readouterr().out == ""


def test_initializer_still_refuses_cluster_or_real_aws_endpoint(monkeypatch):
    path = ROOT / "scripts/init_local_users.py"
    for endpoint in (
        "http://review-dynamodb:8000",
        "https://dynamodb.ap-northeast-1.amazonaws.com",
    ):
        monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", endpoint)
        spec = importlib.util.spec_from_file_location("loopback_initializer", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with pytest.raises(ValueError, match="DynamoDB Local endpoint"):
            module.initialize()


@pytest.mark.parametrize("status", ["failed", "running", "queued"])
def test_acceptance_rejects_non_completed_review(status):
    with pytest.raises(demo.DemoError, match="did not complete"):
        acceptance.completed_review({"status": status})


def test_acceptance_rejects_wrong_model_and_irrelevant_text():
    value = {
        "status": "completed",
        "model_id": acceptance.MODEL,
        "model_revision": acceptance.REVISION,
        "source_code": acceptance.SOURCE,
        "language": "python",
        "review_result": "## Summary\nAverage values.\n## Findings\nUse good names.\n"
        "## Suggestions\nAdd comments.",
    }
    with pytest.raises(demo.DemoError, match="empty-input"):
        acceptance.completed_review(value)
    value["model_revision"] = "0" * 40
    with pytest.raises(demo.DemoError, match="identity"):
        acceptance.completed_review(value)


def test_acceptance_rejects_html_api_fallback():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/html"}, text="<html>SPA</html>"
            )
        ),
        base_url="http://localhost",
    ) as client:
        with pytest.raises(demo.DemoError, match="non-JSON"):
            acceptance.request(client, "GET", "/health/ready", 200)


def test_restart_refuses_to_interrupt_active_jobs(monkeypatch):
    def busy():
        raise demo.DemoError("Active reviews")

    monkeypatch.setattr(acceptance, "require_idle", busy)
    mutate = Mock()
    monkeypatch.setattr(acceptance, "k", mutate)
    with pytest.raises(demo.DemoError, match="Active reviews"):
        acceptance.restart_idle(SimpleNamespace(warm_timeout=600))
    mutate.assert_not_called()


def test_log_allowlist_never_emits_bodies_or_raw_lines(monkeypatch, capsys):
    raw = "\n".join(
        [
            "raw password=secret",
            json.dumps(
                {
                    "event": "review_finished",
                    "outcome": "completed",
                    "source_code": "private source",
                    "cookie": "private cookie",
                    "password": "secret",
                }
            ),
        ]
    )
    monkeypatch.setattr(demo, "k", lambda *a: SimpleNamespace(stdout=raw))
    demo.logs()
    output = capsys.readouterr().out
    assert json.loads(output) == {"event": "review_finished", "outcome": "completed"}
    assert "private" not in output and "secret" not in output


def test_minikube_manifest_contract():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_manifests.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Validated 17 minikube resources" in result.stdout


def test_cold_wait_also_updates_probe_budget():
    value = resource(demo.render(8080, cold_timeout=7200), "Deployment", "review-backend")
    assert value["spec"]["progressDeadlineSeconds"] >= 7200
    assert (
        value["spec"]["template"]["spec"]["containers"][0]["startupProbe"]["failureThreshold"]
        == 720
    )


@pytest.mark.parametrize(
    "failure_stage",
    [
        "image_build",
        "image_load",
        "dependency_apply",
        "dependency_readiness",
        "dependency_initialization",
        "application_apply",
        "backend_readiness",
        "frontend_readiness",
        None,
    ],
)
def test_deployment_records_actual_failure_stage_and_never_claims_false_success(
    monkeypatch, tmp_path, capsys, failure_stage
):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    (tmp_path / "owner.json").write_text("{}")
    # A failed retry must replace a previous success report, not leave it looking current.
    (tmp_path / "startup.json").write_text(
        json.dumps({"status": "ready", "application_ready": True})
    )
    monkeypatch.setattr(demo, "TARGET", {"cluster_uid": "test-uid"})

    def fail_at(name):
        if failure_stage == name:
            raise demo.DemoError("simulated deployment failure")

    owner = {
        "owner": "mine",
        "root": str(ROOT),
        "profile": "minikube",
        "minikube_home": "/test/.minikube",
        "cluster_uid": "test-uid",
    }
    monkeypatch.setattr(demo, "state", lambda **kw: owner)
    monkeypatch.setattr(demo, "guard_cluster", lambda: owner)
    monkeypatch.setattr(demo, "require_local_docker", lambda: None)
    monkeypatch.setattr(demo, "check_images", lambda arch: None)
    monkeypatch.setattr(demo, "ensure_secret", lambda owner: None)
    monkeypatch.setattr(
        state_records, "image_proof", lambda *a: ("sha256:" + "a" * 64, "sha256:" + "b" * 64)
    )

    def rollout(name, seconds):
        phase = {
            "review-dynamodb": "dependency_readiness",
            "review-backend": "backend_readiness",
            "review-frontend": "frontend_readiness",
        }[name]
        fail_at(phase)

    monkeypatch.setattr(demo, "wait_rollout", rollout)
    monkeypatch.setattr(
        demo.subprocess, "call", lambda *a, **kw: 1 if failure_stage == "image_build" else 0
    )
    arch = demo.native_arch(target.platform.machine())
    namespace = {
        "kind": "Namespace",
        "metadata": {"name": demo.NAMESPACE, "uid": "uid", "annotations": {demo.OWNER_KEY: "mine"}},
    }

    def get(kind, name, **kwargs):
        if kind == "namespace":
            return namespace
        if kind == "storageclass":
            return {"provisioner": "k8s.io/minikube-hostpath"}
        return None

    monkeypatch.setattr(demo, "obj", get)
    monkeypatch.setattr(demo, "check_ownership", lambda _: namespace)
    monkeypatch.setattr(demo, "mk", lambda *a, **kw: fail_at("image_load"))

    def execute(args, **kwargs):
        if "daemonset" in args:
            value = json.dumps({"status": {"numberReady": 1}})
        elif args[:3] == ["docker", "container", "inspect"]:
            value = "node-id"
        else:
            value = arch
        return SimpleNamespace(stdout=value)

    monkeypatch.setattr(demo, "run", execute)
    monkeypatch.setattr(
        demo,
        "k",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps({"items": [{"status": {"nodeInfo": {"architecture": arch}}}]})
        ),
    )
    # Rendering is separately exercised against real kubectl in the tests above.
    rendered = [
        {"kind": "Deployment", "metadata": {"name": name}}
        for name in ("review-dynamodb", "review-backend", "review-frontend")
    ]
    monkeypatch.setattr(demo, "render", lambda *a: rendered)
    applied = []

    def apply_step(resources, owner):
        phase = (
            "application_apply"
            if any(r["metadata"]["name"] == "review-backend" for r in resources)
            else "dependency_apply"
        )
        fail_at(phase)
        applied.extend(resources)

    monkeypatch.setattr(demo, "apply", apply_step)
    monkeypatch.setattr(demo, "init_users", lambda: fail_at("dependency_initialization"))
    plan = {
        "architecture": arch,
        "storage_class": "standard",
        "reusable_images": {},
        "fingerprints": {},
        "cni": {"policy_support": "not_enforced"},
        "report": {"status": "failed", "blockers": ["Insufficient target cluster capacity"]},
    }
    monkeypatch.setattr(
        demo, "main", lambda: demo.up(SimpleNamespace(port=8080, cold_timeout=3600), plan)
    )
    assert demo.cli() == (1 if failure_stage else 0)
    report = json.loads((tmp_path / "startup.json").read_text())
    assert report["status"] == ("failed" if failure_stage else "ready")
    assert report["stage"] == (failure_stage or "complete")
    assert report["application_ready"] == (failure_stage is None)
    assert report["preflight"]["status"] == "failed"
    assert report["diagnostic_warnings"] == plan["report"]["blockers"]
    assert not report["review_completed"] and not report["ui_verified"]
    output = capsys.readouterr()
    if failure_stage:
        assert f"Deployment failed during {failure_stage}" in output.err
        assert "Ready is not inference acceptance" not in output.out
    if failure_stage in {"image_build", "image_load", "dependency_apply"}:
        assert not applied
    elif failure_stage in {
        "dependency_readiness",
        "dependency_initialization",
        "application_apply",
    }:
        assert [r["metadata"]["name"] for r in applied] == ["review-dynamodb"]


def profile(name, host="Running"):
    return {"Name": name, "Host": host, "APIServer": "Running", "Kubelet": "Running"}


def test_no_running_cluster_requires_manual_start():
    with pytest.raises(
        demo.DemoError, match="Please start minikube manually before running the deployment command"
    ):
        target.select_profile([profile("stopped", "Stopped")])


def test_single_running_profile_is_selected():
    value = target.select_profile([profile("stopped", "Stopped"), profile("chosen")])
    assert value["Name"] == "chosen"


def test_multiple_running_profiles_require_explicit_choice():
    profiles = [profile("one"), profile("two")]
    with pytest.raises(demo.DemoError, match="--profile NAME"):
        target.select_profile(profiles)
    assert target.select_profile(profiles, "two")["Name"] == "two"


@pytest.mark.parametrize(
    "requested, rows, message",
    [
        ("missing", [], "does not exist"),
        ("stopped", [profile("stopped", "Stopped")], "is not running"),
    ],
)
def test_explicit_missing_or_stopped_profile_fails(requested, rows, message):
    with pytest.raises(demo.DemoError, match=message):
        target.select_profile(rows, requested)


def test_explicit_api_unavailable_fails_before_docker_inspection(monkeypatch, tmp_path):
    item = profile("selected") | {"APIServer": "Stopped"}
    monkeypatch.setattr(target, "discover_profiles", lambda _: [item])
    monkeypatch.setattr(demo, "require_local_docker", lambda: None)
    # Global changes are scoped to the fixture, as they are to one process in the CLI.
    for key in ("PROFILE", "MINIKUBE_HOME", "KUBECONFIG_FILE"):
        monkeypatch.setattr(demo, key, getattr(demo, key))
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    with pytest.raises(demo.DemoError, match="API/kubelet is unavailable"):
        with target.connected_target(SimpleNamespace(minikube_home=tmp_path, profile="selected")):
            pytest.fail("Must not connect")
    calls.assert_not_called()


@pytest.mark.parametrize(
    "verb", ["create", "start", "stop", "delete", "restart", "pause", "unpause", "config", "addons"]
)
def test_minikube_mutation_allowlist_refuses_cluster_lifecycle(monkeypatch, verb):
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    with pytest.raises(demo.DemoError, match="Only minikube image load"):
        demo.mk(verb)
    calls.assert_not_called()


def test_stop_is_advice_only(monkeypatch, capsys):
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    monkeypatch.setattr(target, "connected_target", calls)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "stop"])
    demo.main()
    assert "performs no operations" in capsys.readouterr().out
    calls.assert_not_called()


def test_help_is_english_and_does_not_access_a_cluster(monkeypatch, capsys):
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    monkeypatch.setattr(target, "connected_target", calls)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "--help"])
    with pytest.raises(SystemExit) as result:
        demo.main()
    assert result.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "You manage the cluster; this script deploys the application." in output
    assert "existing running profile" in output
    assert "does not pass persistence acceptance" in output
    assert "doctor exits nonzero for failed or incomplete diagnostics" in output
    assert "up warns and attempts deployment" in output
    calls.assert_not_called()


@pytest.mark.parametrize("unavailable", [None, "", "max"])
def test_memory_report_distinguishes_missing_measurements_from_zero(monkeypatch, unavailable):
    monkeypatch.setattr(
        acceptance,
        "backend_python",
        lambda _: SimpleNamespace(
            stdout=json.dumps({"memory.current": "0", "memory.peak": unavailable})
        ),
    )
    assert acceptance.memory_sample() == {"memory.current": 0, "memory.peak": "not_measured"}


def test_failed_acceptance_saves_unmeasured_fields_without_claiming_success(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    monkeypatch.setattr(demo, "TARGET", {"cluster_uid": "test-uid"})
    monkeypatch.setattr(acceptance.os, "umask", Mock())
    monkeypatch.setattr(acceptance, "runtime_checks", Mock(side_effect=demo.DemoError("blocked")))
    saved = {}
    monkeypatch.setattr(acceptance, "save", lambda name, value: saved.update({name: value}))
    with pytest.raises(demo.DemoError, match="owner.json.root"):
        acceptance.verify({"port": 8080}, SimpleNamespace())
    report = saved["verification.json"]
    assert report["container_memory"] == report["cold_start"] == "not_measured"
    assert not report["review_completed"]
    assert not report["persistence_verified"]
    assert not report["ui_verified"]


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


@pytest.mark.parametrize("owned, metrics_available", [(False, False), (True, False), (True, True)])
def test_preflight_report_uses_stable_english_measurement_statuses(
    diagnostic_environment, owned, metrics_available
):
    diagnostic_environment.update(owned=owned, metrics=metrics_available)
    plan = demo.deployment_plan(SimpleNamespace(port=8080, storage_class=None))
    report = plan["report"]
    assert report["pod_metrics"] == ("available" if metrics_available else "not_measured")
    assert report["own_usage_bytes"] == ("not_measured" if owned and not metrics_available else 0)
    assert report["node_observed_usage_gib"] == 1
    assert bool(report["blockers"]) == (not metrics_available)
    assert report["status"] == ("passed" if metrics_available else "failed")


@pytest.mark.parametrize("command", ["doctor", "up"])
@pytest.mark.parametrize(
    "issue",
    [
        "host_memory",
        "docker_quota",
        "node_capacity",
        "host_disk",
        "vm_disk",
        "missing_metrics",
        "missing_host_memory",
        "missing_docker_stats",
        "malformed_docker_stats",
        "missing_node_limits",
        "missing_workload_inventory",
        "missing_node_allocatable",
        "missing_version",
        "version_skew",
        "not_ready",
        "taint",
        "missing_vm_disk",
        "missing_own_memory",
    ],
)
def test_diagnostics_fail_doctor_but_warn_and_continue_up(
    diagnostic_environment, monkeypatch, capsys, command, issue
):
    diagnostic_environment.update(
        issue=issue,
        metrics=issue not in {"missing_metrics", "missing_own_memory"},
        owned=issue == "missing_own_memory",
    )
    entered = []

    def deploy(args, plan, stage):
        stage("image_build", "review-backend")
        entered.append(plan)
        return {}

    monkeypatch.setattr(demo, "deploy_application", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", command])
    assert demo.cli() == (1 if command == "doctor" else 0)
    output = capsys.readouterr()
    if command == "doctor":
        assert "Diagnostics failed" in output.err
        assert not entered and not demo.STATE.exists()
    else:
        assert entered and "WARNING:" in output.out
        assert "Deployment will still be attempted" in output.out
        report = json.loads((demo.STATE / "startup.json").read_text())
        assert report["status"] == "ready" and report["application_ready"]
        assert report["preflight"]["status"] == "failed"
        assert report["diagnostic_warnings"] == report["preflight"]["blockers"]
        assert not report["review_completed"] and not report["ui_verified"]
        if issue.startswith("missing_") or issue == "malformed_docker_stats":
            assert any(
                m["status"] == "not_measured" for m in report["preflight"]["measurements"].values()
            )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"node_arch": "amd64"}, "architectures differ"),
        ({"docker_arch": "amd64"}, "Docker VM architecture"),
        ({"provisioner": "foreign"}, "StorageClass"),
    ],
)
def test_up_still_blocks_incompatible_architecture_and_storage(
    diagnostic_environment, monkeypatch, change, message, capsys
):
    diagnostic_environment.update(change, issue="host_memory")
    deployment = Mock()
    monkeypatch.setattr(demo, "up", deployment)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    assert message in capsys.readouterr().err
    deployment.assert_not_called()
    assert not demo.STATE.exists()


def test_doctor_succeeds_only_with_complete_sufficient_diagnostics(
    diagnostic_environment, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "doctor"])
    assert demo.cli() == 0
    assert '"status": "passed"' in capsys.readouterr().out
    assert not demo.STATE.exists()


@pytest.mark.parametrize("final_only", [False, True])
def test_report_write_failure_is_nonzero_and_cannot_claim_success(
    diagnostic_environment, monkeypatch, capsys, final_only
):
    plan = demo.deployment_plan(SimpleNamespace(port=8080, storage_class=None))
    demo.STATE.mkdir()
    original = '{"status": "ready", "attempt_id": "old"}'
    (demo.STATE / "startup.json").write_text(original)
    real_save = demo.save

    def cannot_save(name, value):
        if not final_only or value["status"] == "ready":
            raise OSError("private filesystem details")
        real_save(name, value)

    deployment = Mock(return_value={})
    monkeypatch.setattr(demo, "save", cannot_save)
    monkeypatch.setattr(demo, "deploy_application", deployment)
    monkeypatch.setattr(demo, "main", lambda: demo.up(SimpleNamespace(), plan))
    assert demo.cli() == 1
    output = capsys.readouterr()
    assert "private filesystem details" not in output.err
    if final_only:
        report = json.loads((demo.STATE / "startup.json").read_text())
        assert report["status"] == "failed" and report["stage"] == "report_save"
        assert not report["application_ready"]
    else:
        deployment.assert_not_called()
        assert "existing report may be stale" in output.err
        assert (demo.STATE / "startup.json").read_text() == original


@pytest.mark.parametrize("interrupted", [False, True])
def test_unexpected_deployment_error_or_interrupt_is_recorded_and_never_continued(
    diagnostic_environment, monkeypatch, capsys, interrupted
):
    plan = demo.deployment_plan(SimpleNamespace(port=8080, storage_class=None))
    demo.STATE.mkdir()

    def failed(args, plan, stage):
        stage("dependency_initialization")
        raise KeyboardInterrupt() if interrupted else RuntimeError("private response body")

    monkeypatch.setattr(demo, "deploy_application", failed)
    monkeypatch.setattr(demo, "main", lambda: demo.up(SimpleNamespace(), plan))
    assert demo.cli() == (130 if interrupted else 1)
    report_text = (demo.STATE / "startup.json").read_text()
    report = json.loads(report_text)
    assert report["status"] == ("interrupted" if interrupted else "failed")
    assert report["stage"] == "dependency_initialization"
    assert not report["application_ready"]
    assert "private response body" not in report_text + capsys.readouterr().err


def test_up_does_not_swallow_unexpected_diagnostic_exceptions(
    diagnostic_environment, monkeypatch, capsys
):
    monkeypatch.setattr(target, "memory_usage", Mock(side_effect=RuntimeError("private details")))
    deploy = Mock()
    monkeypatch.setattr(demo, "up", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    deploy.assert_not_called()
    assert "private details" not in capsys.readouterr().err
    assert not demo.STATE.exists()


@pytest.mark.parametrize(
    "conflict", ["state", "namespace", "Deployment", "PersistentVolumeClaim", "Secret"]
)
def test_up_ownership_conflicts_stop_before_diagnostics_or_any_state_overwrite(
    monkeypatch, tmp_path, resources, conflict
):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    monkeypatch.setattr(
        demo,
        "TARGET",
        {
            "root": "checkout",
            "profile": "existing",
            "cluster_uid": "cluster",
            "minikube_home": "/existing/.minikube",
        },
    )
    monkeypatch.setattr(demo.os, "environ", dict(demo.os.environ))
    owner = demo.TARGET | {"owner": "ours", "namespace_uid": "namespace"}
    if conflict == "state":
        owner["cluster_uid"] = "foreign-cluster"
    original = json.dumps(owner)
    (tmp_path / "owner.json").write_text(original)
    (tmp_path / "kubeconfig").write_text("original private config")

    @contextlib.contextmanager
    def connected(_):
        yield {}

    def existing(kind, name, **kwargs):
        if kind.lower() == "namespace" or kind == conflict:
            return {
                "kind": kind,
                "metadata": {
                    "name": name,
                    "uid": "namespace",
                    "annotations": {demo.OWNER_KEY: "foreign" if kind == conflict else "ours"},
                },
            }
        return None

    monkeypatch.setattr(target, "connected_target", connected)
    monkeypatch.setattr(demo, "obj", existing)
    monkeypatch.setattr(demo, "render", lambda *a: resources)
    checks = Mock()
    deploy = Mock()
    monkeypatch.setattr(target, "preflight", checks)
    monkeypatch.setattr(demo, "up", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    checks.assert_not_called()
    deploy.assert_not_called()
    assert (tmp_path / "owner.json").read_text() == original
    assert (tmp_path / "kubeconfig").read_text() == "original private config"
    assert not (tmp_path / "startup.json").exists()


def test_up_still_rejects_existing_pvc_class_mismatch(diagnostic_environment, monkeypatch):
    diagnostic_environment["owned"] = True
    monkeypatch.setattr(
        demo,
        "obj",
        lambda kind, *a, **kw: (
            {"provisioner": "k8s.io/minikube-hostpath"}
            if kind == "storageclass"
            else {"spec": {"storageClassName": "foreign"}}
        ),
    )
    deploy = Mock()
    monkeypatch.setattr(demo, "up", deploy)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "up"])
    assert demo.cli() == 1
    deploy.assert_not_called()
    assert not demo.STATE.exists()


def test_target_states_separate_profiles_homes_and_cluster_identity(tmp_path):
    one = target.target_path(tmp_path, Path("/one/.minikube"), "p", "uid-one")
    assert (
        len(
            {
                one,
                target.target_path(tmp_path, Path("/two/.minikube"), "p", "uid-one"),
                target.target_path(tmp_path, Path("/one/.minikube"), "other", "uid-one"),
                target.target_path(tmp_path, Path("/one/.minikube"), "p", "uid-two"),
            }
        )
        == 4
    )
    assert one != tmp_path / "owner.json"


def test_state_identity_mismatch_refuses_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    monkeypatch.setattr(
        demo,
        "TARGET",
        {"root": "here", "profile": "new", "minikube_home": "/home", "cluster_uid": "new"},
    )
    (tmp_path / "owner.json").write_text(json.dumps(demo.TARGET | {"cluster_uid": "old"}))
    with pytest.raises(demo.DemoError, match="another minikube home/profile/cluster"):
        demo.state()
    assert json.loads((tmp_path / "owner.json").read_text())["cluster_uid"] == "old"


def test_existing_unowned_namespace_is_never_adopted(monkeypatch):
    monkeypatch.setattr(demo, "obj", lambda *a, **kw: {"kind": "Namespace", "metadata": {}})
    with pytest.raises(demo.DemoError, match="Ownership conflict"):
        demo.check_ownership(None)


def test_same_inputs_reuse_only_matching_native_image_id(monkeypatch):
    owner = {
        "images": {"review-backend": "review-backend:old"},
        "build_fingerprints": {"review-backend": "fingerprint"},
        "image_ids": {"review-backend": "sha256:expected"},
    }
    calls = Mock(return_value=SimpleNamespace(returncode=0, stdout="arm64 sha256:expected"))
    monkeypatch.setattr(demo, "run", calls)
    assert (
        target.reusable_images(owner, {"review-backend": "fingerprint"}, "arm64") == owner["images"]
    )
    assert target.reusable_images(owner, {"review-backend": "changed"}, "arm64") == {}
    calls.return_value.stdout = "arm64 sha256:replaced"
    assert target.reusable_images(owner, {"review-backend": "fingerprint"}, "arm64") == {}


def test_resource_projection_handles_unbounded_pods_without_fetching_secrets(monkeypatch):
    row = (
        "other\tapp\tnode\tRunning\tuid\t"
        '{"requests":{"memory":"256Mi","cpu":"100m"}}\t\t\tfirst second\t\n'
    )
    calls = Mock(return_value=SimpleNamespace(stdout=row))
    monkeypatch.setattr(demo, "k", calls)
    pods = target.workload_inventory()
    budget = target.sum_budgets([pods[0]["spec"]])
    assert budget["memory_request"] == 256 * 1024**2
    assert budget["unbounded_memory"] == 2
    projection = calls.call_args.args[-1]
    assert "containers[*].resources" in projection
    assert ".env" not in projection and ".command" not in projection


def test_owned_resource_credit_requires_deployment_replicaset_chain(monkeypatch):
    monkeypatch.setattr(
        demo,
        "obj",
        lambda kind, name, **kw: {
            "metadata": {"uid": "owned-deployment", "annotations": {demo.OWNER_KEY: "mine"}}
        },
    )
    monkeypatch.setattr(
        demo,
        "k",
        lambda *a: SimpleNamespace(
            stdout="owned-rs\towned-deployment\nother-rs\tother-deployment\n"
        ),
    )
    pods = [
        {"name": "ours", "namespace": demo.NAMESPACE, "owners": ["owned-rs"]},
        {"name": "foreign", "namespace": demo.NAMESPACE, "owners": ["other-rs"]},
        {"name": "outsider", "namespace": "other", "owners": ["owned-rs"]},
    ]
    assert target.owned_pod_names({"owner": "mine"}, pods) == {"ours"}


def test_cni_without_policy_support_is_reported_not_installed(monkeypatch):
    monkeypatch.setattr(demo, "TARGET", {"node_name": "minikube"})
    calls = Mock(
        return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps({"plugins": [{"type": "bridge"}, {"type": "portmap"}]})
        )
    )
    monkeypatch.setattr(demo, "run", calls)
    assert target.cni_status()["policy_support"] == "not_enforced"
    assert calls.call_args.args[0][:3] == ["docker", "exec", "minikube"]


def test_existing_storage_class_override_is_rendered_without_components():
    resources = demo.render(8080, storage_class="existing-local")
    assert all(
        r["spec"]["storageClassName"] == "existing-local"
        for r in resources
        if r["kind"] == "PersistentVolumeClaim"
    )
    assert not any(
        r["kind"] in {"StorageClass", "DaemonSet", "CustomResourceDefinition"} for r in resources
    )


def test_docker_node_cpu_cap_cannot_be_hidden_by_high_allocatable():
    args = roomy_budget()
    args["node_cpu_cap"] = 2
    assert any("Insufficient target cluster capacity" in e for e in target.capacity_errors(**args))
    assert (
        target.docker_cpu_limit({"nano": 2_000_000_000, "quota": 0, "period": 0, "cpuset": ""}, 10)
        == 2
    )
    assert (
        target.docker_cpu_limit(
            {"nano": 0, "quota": 150000, "period": 100000, "cpuset": "0-3,6"}, 10
        )
        == 1.5
    )


@pytest.mark.parametrize(
    "command",
    ["doctor", "up", "verify", "status", "logs", "port-forward", "import-state", "undeploy"],
)
def test_all_cluster_commands_share_no_running_selection_gate(monkeypatch, tmp_path, command):
    monkeypatch.setattr(sys, "argv", ["minikube_demo", command])
    monkeypatch.setattr(demo, "require_local_docker", lambda: None)
    monkeypatch.setattr(target, "discover_profiles", lambda _: [])
    monkeypatch.setattr(demo, "KUBECONFIG_FILE", tmp_path / "unused")
    monkeypatch.setattr(demo, "MINIKUBE_HOME", tmp_path / ".minikube")
    run = Mock()
    monkeypatch.setattr(demo, "run", run)
    with pytest.raises(
        demo.DemoError, match="Please start minikube manually before running the deployment command"
    ):
        demo.main()
    run.assert_not_called()
    assert not (tmp_path / "unused").exists()


def test_log_allowlist_preserves_safe_startup_diagnostics(monkeypatch, capsys):
    diagnostic = {
        "event": "startup_stage_failed",
        "stage": "cache_validation",
        "error_code": "model_cache_incomplete",
        "exception_type": "ModelCacheIncompleteError",
        "cache_reason": "missing_shard",
        "errno": 2,
    }
    raw = json.dumps({**diagnostic, "exception": "private URL/token", "prompt": "private prompt"})
    monkeypatch.setattr(demo, "k", lambda *a: SimpleNamespace(stdout=raw))
    demo.logs()
    assert json.loads(capsys.readouterr().out) == diagnostic
