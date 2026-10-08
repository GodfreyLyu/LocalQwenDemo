"""Offline regression tests; these are never evidence of real-model acceptance."""

import json
import subprocess
import sys
from pathlib import PurePosixPath
from types import SimpleNamespace

import deployment.legacy.runtime as demo  # noqa: E402
import deployment.legacy.verify as acceptance  # noqa: E402
import pytest
import yaml

from scripts.tests.support.deployment import permission_bits, resource
from scripts.tests.support.paths import ROOT


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.contract
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
    assert not backend.get("env")
    assert backend["envFrom"] == [
        {"configMapRef": {"name": "review-config"}},
        {"secretRef": {"name": "review-secrets"}},
    ]
    assert (
        cfg.items()
        >= {
            "OLLAMA_MODEL": "qwen3:1.7b",
            "MODEL_CONTEXT_TOKENS": "4096",
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.contract
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
            "OLLAMA_MODEL": acceptance.MODEL,
            "OLLAMA_MODEL_DIGEST": acceptance.REVISION,
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.contract
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.security
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.security
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
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


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.contract
def test_minikube_manifest_contract():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_manifests.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Validated 17 minikube resources" in result.stdout


@pytest.mark.integration
@pytest.mark.requires_kubectl
def test_cold_wait_also_updates_probe_budget():
    value = resource(demo.render(8080, cold_timeout=7200), "Deployment", "review-backend")
    assert value["spec"]["progressDeadlineSeconds"] >= 7200
    assert (
        value["spec"]["template"]["spec"]["containers"][0]["startupProbe"]["failureThreshold"]
        == 720
    )


@pytest.mark.integration
@pytest.mark.requires_kubectl
@pytest.mark.contract
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
