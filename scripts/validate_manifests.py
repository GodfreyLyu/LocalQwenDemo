"""Offline checks of the actual minikube render; never connect to a cluster."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def validate():
    rendered = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "deploy/kustomize/overlays/minikube")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    resources = list(yaml.safe_load_all(rendered))
    assert len(resources) == 17
    assert all(r["metadata"]["namespace"] == "local-review-demo" for r in resources)
    assert {r["kind"] for r in resources} <= {
        "Deployment",
        "Service",
        "ServiceAccount",
        "ConfigMap",
        "NetworkPolicy",
        "PersistentVolumeClaim",
    }
    by_name = {(r["kind"], r["metadata"]["name"]): r for r in resources}
    cfg = by_name["ConfigMap", "review-config"]["data"]
    assert (
        cfg.items()
        >= {
            "MODEL_ID": "Qwen/Qwen3-1.7B",
            "MODEL_REVISION": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
            "MODEL_DTYPE": "bfloat16",
            "MODEL_CPU_THREADS": "2",
            "MODEL_INFERENCE_CONCURRENCY": "1",
            "MODEL_MAX_INPUT_TOKENS": "2048",
            "MODEL_MAX_OUTPUT_TOKENS": "384",
            "INFERENCE_TIMEOUT_SECONDS": "300",
            "QUEUE_CAPACITY": "8",
            "MAX_RETRIES": "1",
            "ENVIRONMENT": "local",
            "ALLOWED_ORIGIN": "http://localhost:8080",
            "COOKIE_SECURE": "false",
            "DYNAMODB_ENDPOINT_URL": "http://review-dynamodb:8000",
            "DYNAMODB_TABLE": "llm-review-users",
            "AWS_ACCESS_KEY_ID": "local",
            "AWS_SECRET_ACCESS_KEY": "local",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
        }.items()
    )
    for resource in resources:
        if resource["kind"] != "Deployment":
            continue
        spec = resource["spec"]
        assert spec["replicas"] == 1 and spec["strategy"]["type"] == "Recreate"
        pod = spec["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["runAsNonRoot"]
        assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
        for container in pod["containers"]:
            security = container["securityContext"]
            assert security["readOnlyRootFilesystem"] and not security["allowPrivilegeEscalation"]
            assert security["capabilities"] == {"drop": ["ALL"]}
            assert all(
                probe in container for probe in ["startupProbe", "readinessProbe", "livenessProbe"]
            )
            assert all(
                key in container["resources"][group]
                for group in ("requests", "limits")
                for key in ("cpu", "memory", "ephemeral-storage")
            )
        for init in pod.get("initContainers", []):
            assert init["name"] == "volume-permissions"
            assert init["securityContext"]["capabilities"] == {
                "drop": ["ALL"],
                "add": ["CHOWN", "FOWNER"],
            }
            assert init["securityContext"]["readOnlyRootFilesystem"]
            assert init["securityContext"]["allowPrivilegeEscalation"] is False
    backend = by_name["Deployment", "review-backend"]["spec"]["template"]["spec"]
    container = backend["containers"][0]
    assert container["env"] == [{"name": "OMP_NUM_THREADS", "value": "2"}]
    assert container["resources"] == {
        "requests": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "256Mi"},
        "limits": {"cpu": "2", "memory": "6Gi", "ephemeral-storage": "1Gi"},
    }
    assert backend["securityContext"]["runAsUser"] == 10001
    dynamodb = by_name["Deployment", "review-dynamodb"]["spec"]["template"]["spec"]
    assert dynamodb["securityContext"]["runAsUser"] == 1000
    assert dynamodb["securityContext"]["fsGroup"] == 10001
    assert dynamodb["containers"][0]["image"] == "amazon/dynamodb-local:3.1.0"
    claims = {
        r["metadata"]["name"]: r["spec"] for r in resources if r["kind"] == "PersistentVolumeClaim"
    }
    assert set(claims) == {"review-model-cache", "review-history", "review-dynamodb"}
    for name, size in {
        "review-history": "10Gi",
        "review-model-cache": "12Gi",
        "review-dynamodb": "1Gi",
    }.items():
        assert claims[name]["storageClassName"] == "standard"
        assert claims[name]["accessModes"] == ["ReadWriteOnce"]
        assert claims[name]["resources"]["requests"]["storage"] == size
    print(
        "Validated 17 minikube resources, fixed inference settings, local transport and hardening."
    )


if __name__ == "__main__":
    validate()
