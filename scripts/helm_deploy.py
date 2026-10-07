"""Deploy a reviewed Helm snapshot into an explicitly selected existing Minikube."""

import argparse
import base64
import json
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

import yaml
from helm_target import check_ownership, connect, pod_requests, quantity, require, run

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy/helm/local-review"


def install(target, args):
    values = []
    if (ROOT / "release-values.yaml").exists():
        values += ["-f", str(ROOT / "release-values.yaml")]
    for path in args.values:
        values += ["-f", str(Path(path).resolve())]
    base = [
        "helm",
        "template",
        args.release,
        str(CHART),
        "--namespace",
        args.namespace,
        *values,
    ]
    # Inspect model configuration before discovering the legacy wrapper's host CIDR.
    rendered = list(yaml.safe_load_all(run(*base, "--set", "networkPolicy.enabled=false").stdout))
    cfg = next(
        r["data"]
        for r in rendered
        if r["kind"] == "ConfigMap" and r["metadata"]["name"] == "review-config"
    )
    host_ip = None
    if cfg["OLLAMA_BASE_URL"] == "http://host.minikube.internal:11434":
        host_ip = target.ollama_ip()
        base += [
            "--set-string",
            "networkPolicy.ollamaHostCidr=" + host_ip + "/32",
            "--set-string",
            "networkPolicy.ollamaNamespace=",
        ]
    # Include policies in ownership checks once the actual host address is known.
    rendered = list(yaml.safe_load_all(run(*base).stdout))
    backend = next(
        r
        for r in rendered
        if r["kind"] == "Deployment" and r["metadata"]["name"] == "review-backend"
    )
    secret_name = backend["spec"]["template"]["spec"]["containers"][0]["envFrom"][1]["secretRef"][
        "name"
    ]
    node = target.kubectl("get", "nodes", "-o", "json")
    nodes = json.loads(node.stdout)["items"]
    require(
        len(nodes) == 1
        and any(
            c["type"] == "Ready" and c["status"] == "True" for c in nodes[0]["status"]["conditions"]
        ),
        "Selected node must be Ready",
    )
    pods = json.loads(target.kubectl("get", "pods", "--all-namespaces", "-o", "json").stdout)[
        "items"
    ]
    for resource_name in ("cpu", "memory"):
        requested = sum(
            pod_requests(r["spec"]["template"]["spec"], resource_name)
            for r in rendered
            if r["kind"] == "Deployment"
        )
        other = sum(
            pod_requests(p["spec"], resource_name)
            for p in pods
            if p.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
            and not (
                p["metadata"]["namespace"] == args.namespace
                and p["metadata"].get("labels", {}).get("app.kubernetes.io/instance")
                == args.release
            )
        )
        available = quantity(nodes[0]["status"]["allocatable"][resource_name])
        require(
            requested + other <= available,
            f"Insufficient allocatable {resource_name} for requested resources",
        )
    for resource in rendered:
        if resource["kind"] == "PersistentVolumeClaim":
            sc = target.object("storageclass", resource["spec"]["storageClassName"])
            require(
                sc and sc["provisioner"] == "k8s.io/minikube-hostpath",
                "Expected an existing Minikube hostpath StorageClass",
            )
        existing = target.object(resource["kind"], resource["metadata"]["name"])
        if existing:
            check_ownership(existing, args.release, args.namespace)
    # Check all externally supplied claims before creating workloads.
    generated_claims = {
        r["metadata"]["name"] for r in rendered if r["kind"] == "PersistentVolumeClaim"
    }
    for resource in rendered:
        if resource["kind"] == "Deployment":
            for volume in resource["spec"]["template"]["spec"].get("volumes", []):
                if "persistentVolumeClaim" in volume:
                    name = volume["persistentVolumeClaim"]["claimName"]
                    if name not in generated_claims:
                        require(
                            target.object("pvc", name) is not None,
                            f"External PVC {name} is missing",
                        )
    overrides = {"config": {"allowedOrigin": f"http://localhost:{args.port}"}}
    if host_ip:
        overrides["networkPolicy"] = {
            "ollamaHostCidr": host_ip + "/32",
            "ollamaNamespace": "",
        }
    # Secret values are neither command-line arguments nor output. Never rotate an existing key.
    if target.object("namespace", args.namespace) is None:
        target.kubectl("create", "namespace", args.namespace)
    secret_valid = target.kubectl(
        "get",
        "secret",
        secret_name,
        "--ignore-not-found",
        "-o",
        "go-template={{if .data.SIGNING_SECRET}}"
        "{{ge (len (.data.SIGNING_SECRET | base64decode)) 32}}{{else}}false{{end}}",
    ).stdout.strip()
    if secret_valid:
        require(
            secret_valid == "true",
            "Existing signing Secret is invalid; refusing rotation",
        )
    else:
        target.kubectl(
            "create",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {"name": secret_name, "namespace": args.namespace},
                    "data": {
                        "SIGNING_SECRET": base64.b64encode(
                            secrets.token_urlsafe(48).encode()
                        ).decode()
                    },
                }
            ),
        )
    with tempfile.TemporaryDirectory(prefix="review-values-") as scratch:
        path = Path(scratch) / "target.json"
        path.write_text(json.dumps(overrides))
        if host_ip:
            require(
                target.ollama_ip() == host_ip,
                "Ollama address changed during preflight; retry",
            )
        result = run(
            *target.helm,
            "upgrade",
            "--install",
            args.release,
            str(CHART),
            *values,
            "-f",
            path,
            "--wait",
            "--timeout",
            f"{args.timeout}s",
            timeout=args.timeout + 60,
            env=target.env,
        )
        print(result.stdout)


def main():
    print("Deprecated wrapper: use standard Helm or minikube_demo.sh for local builds.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "status", "port-forward", "uninstall"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--namespace", default="local-review-demo")
    parser.add_argument("--release", default="local-review")
    parser.add_argument("--minikube-home")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("-f", "--values", action="append", default=[])
    args = parser.parse_args()
    require(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,51}[a-z0-9])?", args.release),
        "Invalid release name",
    )
    require(
        1024 <= args.port <= 65535 and 60 <= args.timeout <= 7200,
        "Invalid port or timeout",
    )
    with connect(args.profile, args.namespace, args.minikube_home) as target:
        if args.command == "install":
            install(target, args)
        elif args.command == "port-forward":
            service = target.object("service", "review-frontend")
            require(service is not None, "Frontend Service is missing")
            check_ownership(service, args.release, args.namespace)
            subprocess.run(
                [
                    *target.kube,
                    "port-forward",
                    "--address",
                    "127.0.0.1",
                    "service/review-frontend",
                    f"{args.port}:8080",
                ],
                env=target.env,
                check=True,
            )
        else:
            command = [*target.helm, args.command, args.release]
            if args.command == "uninstall":
                command += ["--wait", "--timeout", f"{args.timeout}s"]
            print(run(*command, env=target.env, timeout=args.timeout + 60).stdout)


if __name__ == "__main__":
    main()
