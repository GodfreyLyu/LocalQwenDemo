"""Offline checks for source Charts and complete deployment-release snapshots."""

import argparse
import ipaddress
import json
import re
import subprocess
from pathlib import Path

import yaml

CHART = Path("deploy/helm/local-review")


def validate_tree(root):
    allowed = {
        "README.md",
        "release.json",
        "release-values.yaml",
        "scripts/helm_deploy.py",
        "scripts/helm_target.py",
        "scripts/validate_helm.py",
        "docs/guides/helm-release.md",
        ".github/workflows/deployment-validation.yml",
    }
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        require(not path.is_symlink(), "Release snapshots must not contain symlinks")
        if path.is_file():
            require(
                relative in allowed or relative.startswith(CHART.as_posix() + "/"),
                f"Unexpected release file: {relative}",
            )


def require(condition, message):
    if not condition:
        raise ValueError(message)


def render(root, values=(), settings=()):
    command = [
        "helm",
        "template",
        "local-review",
        str(root / CHART),
        "--namespace",
        "review-validation",
    ]
    for path in values:
        command += ["-f", str(path)]
    for setting in settings:
        command += ["--set", setting]
    return list(yaml.safe_load_all(subprocess.check_output(command, text=True)))


def validate_resources(resources):
    allowed = {
        "Deployment",
        "Service",
        "ConfigMap",
        "PersistentVolumeClaim",
        "ServiceAccount",
        "NetworkPolicy",
    }
    require(
        all(r and r["kind"] in allowed for r in resources),
        "Unexpected Kubernetes resource",
    )
    keyed = {(r["kind"], r["metadata"]["name"]): r for r in resources}
    require(len(keyed) == len(resources), "Duplicate Kubernetes resource")
    for component in ("backend", "frontend", "dynamodb"):
        name = "review-" + component
        deployment = keyed["Deployment", name]["spec"]
        require(deployment["replicas"] == 1, "Only single-replica deployments are supported")
        require(
            deployment["strategy"]["type"] == "Recreate",
            "Recreate must preserve worker ownership",
        )
        pod = deployment["template"]["spec"]
        require(
            not pod["automountServiceAccountToken"],
            "Service account token must not be mounted",
        )
        require(pod["securityContext"]["runAsNonRoot"], "Application must run as non-root")
        for container in pod["containers"]:
            security = container["securityContext"]
            require(
                security["readOnlyRootFilesystem"] and not security["allowPrivilegeEscalation"],
                "Container hardening missing",
            )
            require(
                security["capabilities"]["drop"] == ["ALL"],
                "Container capabilities must be dropped",
            )
            require(
                all(p in container for p in ("startupProbe", "readinessProbe", "livenessProbe")),
                "Probes missing",
            )
        require(
            keyed["Service", name]["spec"]["selector"] == deployment["selector"]["matchLabels"],
            "Service/Deployment selector mismatch",
        )
    backend = keyed["Deployment", "review-backend"]["spec"]["template"]["spec"]
    initialize = [c for c in backend["initContainers"] if c["name"] == "initialize-users"]
    require(
        len(initialize) == 1
        and initialize[0]["command"] == ["python", "-m", "app.persistence.initialize_users"],
        "Users initialization must precede API startup",
    )
    require(
        initialize[0]["image"] == backend["containers"][0]["image"],
        "Initializer must use the backend image",
    )
    config = keyed["ConfigMap", "review-config"]["data"]
    require(
        all(isinstance(v, str) for v in config.values()),
        "ConfigMap values must be strings",
    )
    require(
        config["MODEL_INFERENCE_CONCURRENCY"] == "1",
        "Inference concurrency must remain one",
    )
    require(
        config["DYNAMODB_ENDPOINT_URL"] == "http://review-dynamodb:8000",
        "DynamoDB endpoint must remain local",
    )
    for resource in resources:
        if resource["kind"] == "PersistentVolumeClaim":
            require(
                resource["metadata"]["annotations"].get("helm.sh/resource-policy") == "keep",
                "PVC retention is required",
            )
        if resource["kind"] == "NetworkPolicy":
            require(
                all(p in {"Ingress", "Egress"} for p in resource["spec"]["policyTypes"]),
                "Invalid network policy types",
            )
            for rule in resource["spec"].get("egress", []):
                if any(p.get("port") == 11434 for p in rule.get("ports", [])):
                    for target in rule["to"]:
                        network = ipaddress.ip_network(target["ipBlock"]["cidr"])
                        private = any(
                            network.subnet_of(ipaddress.ip_network(n))
                            for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
                        )
                        require(
                            network.version == 4 and network.prefixlen == 32 and private,
                            "Ollama egress must target one private IPv4 address",
                        )
    return keyed


def validate_snapshot(root, keyed):
    manifest = json.loads((root / "release.json").read_text())
    require(manifest["schemaVersion"] in {1, 2}, "Unknown release manifest format")
    if manifest["schemaVersion"] == 2:
        candidate = manifest.get("candidate", {})
        number = candidate.get("runNumber")
        version = candidate.get("version", "")
        require(type(number) is int and number > 0, "Invalid candidate run number")
        require(
            re.fullmatch(r"[1-9][0-9]*", candidate.get("runId", "")), "Invalid candidate run ID"
        )
        require(
            re.fullmatch(
                r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\." + str(number), version
            ),
            "Invalid candidate version",
        )
        require(
            candidate.get("branch") == "release-candidate/" + version,
            "Candidate branch does not match version",
        )
        require(
            re.fullmatch(r"[a-f0-9]{40}", candidate.get("baseSha", "")), "Invalid candidate base"
        )
        require(manifest["chartVersion"] == version, "Candidate version does not match Chart")
    require(re.fullmatch(r"[a-f0-9]{40}", manifest["sourceSha"]), "Invalid source commit")
    chart = yaml.safe_load((root / CHART / "Chart.yaml").read_text())
    require(
        chart["version"] == manifest["chartVersion"],
        "Chart version does not match provenance",
    )
    require(
        chart["appVersion"] == manifest["sourceSha"],
        "Chart source does not match provenance",
    )
    require(
        keyed["ConfigMap", "review-config"]["data"]["RELEASE_SHA"] == manifest["sourceSha"],
        "Runtime source does not match provenance",
    )
    for component in ("backend", "frontend"):
        entry = manifest["images"][component]
        require(
            re.fullmatch(r"sha256:[a-f0-9]{64}", entry["digest"]),
            "Invalid image digest",
        )
        require(re.fullmatch(r"[a-f0-9]{40}", entry["sourceSha"]), "Invalid image source")
        image = keyed["Deployment", "review-" + component]["spec"]["template"]["spec"][
            "containers"
        ][0]["image"]
        require(
            image == entry["repository"] + "@" + entry["digest"],
            "Deployment image does not match provenance",
        )
    for directory in ("backend", "frontend"):
        require(
            not (root / directory).exists(),
            "Source code must not be copied into release branch",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.snapshot:
        validate_tree(root)
    values = [root / "release-values.yaml"] if args.snapshot else []
    subprocess.run(
        ["helm", "lint", str(root / CHART), *sum((["-f", str(v)] for v in values), [])],
        check=True,
    )
    keyed = validate_resources(render(root, values))
    if args.snapshot:
        validate_snapshot(root, keyed)
    else:
        validate_resources(
            render(
                root,
                settings=[
                    "model.backend=transformers",
                    "volumePermissions.enabled=false",
                ],
            )
        )
        validate_resources(
            render(
                root,
                settings=[
                    "networkPolicy.ollamaHostCidr=192.168.49.1/32",
                    "persistence.history.existingClaim=retained-history",
                ],
            )
        )
    print("Helm configuration and deployment invariants validated.")


if __name__ == "__main__":
    main()
