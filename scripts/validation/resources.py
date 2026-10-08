"""Resource invariants shared by Helm and GitOps validation."""

import ipaddress


def require(condition, message):
    if not condition:
        raise ValueError(message)


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
                        if "namespaceSelector" in target:
                            require(
                                bool(
                                    target["namespaceSelector"]
                                    .get("matchLabels", {})
                                    .get("kubernetes.io/metadata.name")
                                )
                                and target.get("podSelector", {})
                                .get("matchLabels", {})
                                .get("app.kubernetes.io/name")
                                == "local-ollama"
                                and bool(
                                    target["podSelector"]["matchLabels"].get(
                                        "app.kubernetes.io/instance"
                                    )
                                ),
                                "Cluster Ollama egress must select one namespace and release",
                            )
                            continue
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
