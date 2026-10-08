"""Helm rendering, container hardening and actual DynamoDB image permissions."""

import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from deployment.common import acceptance
from validation.constants import MINIKUBE_VALUES
from validation.helm import render

from scripts.tests.support.deployment import permission_bits, resource
from scripts.tests.support.paths import ROOT


@pytest.fixture(scope="module")
def resources():
    return render(ROOT, [ROOT / MINIKUBE_VALUES])


@pytest.mark.integration
@pytest.mark.requires_helm
@pytest.mark.contract
def test_inference_configuration_stays_in_backend_config(resources):
    rendered = resources
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
@pytest.mark.requires_helm
@pytest.mark.contract
def test_chart_preserves_product_contract_and_has_no_cloud_resources(resources):
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
    assert all(r["metadata"]["namespace"] == "review-validation" for r in resources)
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
@pytest.mark.requires_helm
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
        if init["name"] != "volume-permissions":
            continue
        assert init["securityContext"]["capabilities"]["add"] == ["CHOWN", "FOWNER"]
        assert "os.chown" in init["args"][0] and "rmtree" not in init["args"][0]


@pytest.mark.integration
@pytest.mark.requires_helm
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
@pytest.mark.requires_helm
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
@pytest.mark.requires_helm
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
@pytest.mark.requires_helm
@pytest.mark.contract
def test_kubernetes_config_map_values_load_from_environment(monkeypatch, resources):
    from app.config import Settings

    config = resource(resources, "ConfigMap", "review-config")["data"]
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    settings = Settings(signing_secret="test-secret-" * 4)
    assert settings.dynamodb_endpoint_url == "http://review-dynamodb:8000"
    assert settings.environment == "local"
    assert settings.cookie_secure is False
    assert settings.model_inference_concurrency == 1
    assert settings.ollama_model == "qwen3:1.7b"
    assert settings.model_context_tokens == 4096
    assert settings.model_max_output_tokens == 384
    assert settings.inference_timeout_seconds == 300
    assert settings.data_dir == Path("/data")
    assert settings.release_sha is None
