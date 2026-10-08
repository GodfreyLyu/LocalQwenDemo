import json
import subprocess

from deployment.common.target import pod_requests, quantity, require, run


def doctor(session, values, resources):
    nodes = json.loads(session.target.kubectl("get", "nodes", "-o", "json").stdout)["items"]
    require(len(nodes) == 1, "Expected one Minikube node.")
    node = nodes[0]
    require(
        any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"]),
        "Node is not Ready.",
    )
    arch = node["status"]["nodeInfo"]["architecture"]
    require(arch in {"arm64", "amd64"}, "Unsupported node architecture.")
    limits = json.loads(
        run("docker", "inspect", session.target.node, env=session.target.env).stdout
    )[0]["HostConfig"]
    pods = json.loads(session.target.kubectl("get", "pods", "-A", "-o", "json").stdout)["items"]
    warnings = []
    capacity = {}
    for key in ("cpu", "memory"):
        amount = quantity(node["status"]["allocatable"][key])
        container_limit = (
            limits.get("NanoCpus", 0) / 1e9 if key == "cpu" else limits.get("Memory", 0)
        )
        if key == "cpu" and limits.get("CpuQuota", 0) > 0 and limits.get("CpuPeriod", 0) > 0:
            quota = limits["CpuQuota"] / limits["CpuPeriod"]
            container_limit = min(container_limit, quota) if container_limit else quota
        if container_limit:
            amount = min(amount, container_limit)
        other = sum(
            pod_requests(p["spec"], key)
            for p in pods
            if p.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
            and not (
                p["metadata"]["namespace"] == session.namespace
                and p["metadata"].get("labels", {}).get("app.kubernetes.io/instance")
                == session.release
            )
        )
        needed = sum(
            pod_requests(r["spec"]["template"]["spec"], key)
            for r in resources
            if r["kind"] == "Deployment"
        )
        capacity[key] = {
            "capacity": amount,
            "other_requests": other,
            "application_requests": needed,
        }
        if needed + other > amount:
            warnings.append(f"Insufficient {key} capacity for configured requests")
    if ".svc.cluster.local:" not in values["model"]["ollamaBaseUrl"]:
        # Cluster DNS is resolved in Pods; their readiness verifies Service access.
        # Probe host endpoints from the selected Docker node only.
        try:
            run(
                "docker",
                "exec",
                session.target.node,
                "curl",
                "--fail",
                "--silent",
                "--max-time",
                "10",
                values["model"]["ollamaBaseUrl"].rstrip("/") + "/api/tags",
                env=session.target.env,
            )
        except (RuntimeError, subprocess.TimeoutExpired):
            warnings.append("Ollama is unreachable from the node; Pod readiness must verify access")
    sc = session.target.object("storageclass", values["persistence"]["storageClass"])
    if not sc:
        warnings.append("Configured StorageClass is missing")
    run("helm", "version", "--short", env=session.target.env)
    report = {"architecture": arch, "resources": capacity, "warnings": warnings}
    print(json.dumps(report, indent=2))
    return report
