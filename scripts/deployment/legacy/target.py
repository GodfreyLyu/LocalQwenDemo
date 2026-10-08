"""Read-only discovery and incremental capacity planning for a user-managed minikube.

Called by minikube_demo after parsing the target options. Discovery creates only
a temporary private kubeconfig; mandatory target/storage checks are separate from
advisory capacity measurements. Missing measurements never imply spare capacity.
"""

import base64
import contextlib
import hashlib
import json
import platform
import re
import shutil
import tempfile
from pathlib import Path

from deployment.legacy.build import reusable_images as reusable_images
from deployment.legacy.build import source_fingerprint as source_fingerprint
from deployment.legacy.capacity import capacity_errors as capacity_errors
from deployment.legacy.capacity import docker_cpu_limit as docker_cpu_limit
from deployment.legacy.capacity import docker_usage as docker_usage
from deployment.legacy.capacity import quantity as quantity
from deployment.legacy.capacity import resources_of as resources_of
from deployment.legacy.capacity import sum_budgets as sum_budgets
from deployment.legacy.context import api

GIB = 1024**3


def normalized_home(path):
    home = Path(path).expanduser().resolve()
    return home if home.name == ".minikube" else home / ".minikube"


def select_profile(profiles, requested=None):
    """Select an explicit or uniquely running profile; never use current-context."""
    d = api()
    if requested:
        matches = [p for p in profiles if p["Name"] == requested]
        d.require(matches, f"The specified profile {requested!r} does not exist.")
        chosen = matches[0]
        d.require(
            chosen.get("Host") == "Running",
            f"Profile {requested!r} is not running. "
            "Please start minikube manually before running the deployment command.",
        )
        return chosen
    running = [p for p in profiles if p.get("Host") == "Running"]
    d.require(
        running,
        "Please start minikube manually before running the deployment command. "
        "No running cluster was detected.",
    )
    d.require(
        len(running) == 1,
        "Multiple clusters are running; select one explicitly with --profile NAME: "
        + ", ".join(p["Name"] for p in running),
    )
    return running[0]


def discover_profiles(home):
    d = api()
    if not (home / "profiles").is_dir():
        return []
    # No audit files or global kubeconfig writes. Only existing profile state is inspected.
    result = d.run(["minikube", "--skip-audit", "profile", "list", "-o", "json"], check=False)
    try:
        listing = json.loads(result.stdout)
    except ValueError:
        raise d.DemoError(
            "Unable to read local minikube profiles; check the Docker daemon and minikube."
        ) from None
    profiles = listing.get("valid", []) + listing.get("invalid", [])
    for profile in profiles:
        name = profile.get("Name", "")
        d.require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name), "Invalid profile name.")
        status = d.run(
            ["minikube", "--skip-audit", "-p", name, "status", "-o", "json"], check=False
        )
        try:
            profile.update(json.loads(status.stdout))
        except ValueError:
            profile["Host"] = "Unknown"
        print(
            f"Profile {name}: host={profile.get('Host', 'Unknown')}, "
            f"API={profile.get('APIServer', 'Unknown')}"
        )
    return profiles


def target_path(root, home, profile, cluster_uid):
    scope = hashlib.sha256(str(home).encode()).hexdigest()[:12]
    api().require(re.fullmatch(r"[a-zA-Z0-9-]+", cluster_uid), "Invalid cluster identity.")
    return root / "targets" / f"{profile}-{scope}" / cluster_uid


@contextlib.contextmanager
def connected_target(args):
    """Use a temporary private kubeconfig, then identify the existing API server read-only."""
    d = api()
    home = normalized_home(args.minikube_home)
    d.MINIKUBE_HOME = home
    with tempfile.TemporaryDirectory(prefix="review-target-") as scratch:
        d.KUBECONFIG_FILE = Path(scratch) / "kubeconfig"
        d.require_local_docker()
        chosen = select_profile(discover_profiles(home), args.profile)
        d.PROFILE = chosen["Name"]
        d.require(
            chosen.get("APIServer") == "Running" and chosen.get("Kubelet") == "Running",
            f"The Kubernetes API/kubelet is unavailable for profile {d.PROFILE!r}; "
            "repair the cluster manually.",
        )
        config = chosen.get("Config", {})
        d.require(
            config.get("Driver") == "docker",
            "Only local minikube clusters using the Docker driver are supported.",
        )
        d.require(
            len(config.get("Nodes", [])) == 1,
            "The persistent volume setup requires an existing single-node cluster.",
        )
        node_name = config["Nodes"][0].get("Name") or d.PROFILE
        template = '{"ports":{{json .NetworkSettings.Ports}},"labels":{{json .Config.Labels}}}'
        info = json.loads(
            d.run(["docker", "container", "inspect", "--format", template, node_name]).stdout
        )
        d.require(
            info["labels"].get("name.minikube.sigs.k8s.io") == d.PROFILE,
            "Docker node does not match selected minikube profile.",
        )
        bindings = info["ports"].get("8443/tcp") or []
        loopback = [b for b in bindings if b["HostIp"] == "127.0.0.1"]
        d.require(
            len(loopback) == 1, "No unique loopback API binding; refusing remote/unverified API."
        )
        port = int(loopback[0]["HostPort"])
        cert_dir = home / "profiles" / d.PROFILE
        paths = (home / "ca.crt", cert_dir / "client.crt", cert_dir / "client.key")
        d.require(
            all(p.is_file() for p in paths),
            "Local certificates for the selected profile are incomplete.",
        )
        ca, cert, key = [base64.b64encode(p.read_bytes()).decode() for p in paths]
        cfg = {
            "apiVersion": "v1",
            "kind": "Config",
            "current-context": d.PROFILE,
            "clusters": [
                {
                    "name": d.PROFILE,
                    "cluster": {
                        "server": f"https://127.0.0.1:{port}",
                        "certificate-authority-data": ca,
                    },
                }
            ],
            "users": [
                {
                    "name": d.PROFILE,
                    "user": {"client-certificate-data": cert, "client-key-data": key},
                }
            ],
            "contexts": [
                {
                    "name": d.PROFILE,
                    "context": {"cluster": d.PROFILE, "user": d.PROFILE, "namespace": d.NAMESPACE},
                }
            ],
        }
        d.KUBECONFIG_FILE.write_text(json.dumps(cfg))
        d.KUBECONFIG_FILE.chmod(0o600)
        ready = d.k("get", "--raw=/readyz", "--request-timeout=10s", check=False)
        d.require(
            ready.returncode == 0 and ready.stdout.strip() == "ok",
            f"The Kubernetes API is unavailable for profile {d.PROFILE!r} "
            "(local certificates/TLS/readyz).",
        )
        uid = d.k("get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}").stdout
        d.STATE = target_path(d.STATE_ROOT, home, d.PROFILE, uid)
        d.TARGET = {
            "profile": d.PROFILE,
            "minikube_home": str(home),
            "cluster_uid": uid,
            "node_name": node_name,
            "root": str(d.ROOT),
        }
        print(f"Selected running profile: {d.PROFILE}; cluster UID: {uid}")
        print(f"Application state: {d.STATE}")
        yield cfg


def decode_objects(text):
    objects = []
    decoder = json.JSONDecoder()
    text = text.strip()
    while text:
        item, end = decoder.raw_decode(text)
        objects.append(item)
        text = text[end:].lstrip()
    return objects


def workload_inventory():
    # Project resource fields only: do not retrieve other Pods' env/args/annotations.
    fields = [
        ".metadata.namespace",
        ".metadata.name",
        ".spec.nodeName",
        ".status.phase",
        ".metadata.ownerReferences[*].uid",
        ".spec.containers[*].resources",
        ".spec.initContainers[*].resources",
        ".spec.overhead",
        ".spec.containers[*].name",
        ".spec.initContainers[*].name",
    ]
    projection = (
        "{range .items[*]}" + '{"\\t"}'.join("{" + f + "}" for f in fields) + '{"\\n"}{end}'
    )
    rows = api().k("get", "pods", "--all-namespaces", "-o", "jsonpath=" + projection).stdout
    pods = []
    for line in rows.splitlines():
        ns, name, node, phase, owners, containers, init, overhead, names, init_names = line.split(
            "\t"
        )
        if phase in {"Succeeded", "Failed"}:
            continue
        regular = decode_objects(containers)
        initial = decode_objects(init)
        regular += [{}] * (len(names.split()) - len(regular))
        initial += [{}] * (len(init_names.split()) - len(initial))
        pods.append(
            {
                "namespace": ns,
                "name": name,
                "node": node,
                "owners": owners.split(),
                "spec": {
                    "containers": [{"resources": r} for r in regular],
                    "initContainers": [{"resources": r} for r in initial],
                    "overhead": json.loads(overhead) if overhead else {},
                },
            }
        )
    return pods


def owned_pod_names(owner, pods):
    if not owner:
        return set()
    d = api()
    deployments = {
        o["metadata"]["uid"]
        for name in ("review-backend", "review-frontend", "review-dynamodb")
        if (o := d.obj("deployment", name, optional=True))
        and o["metadata"].get("annotations", {}).get(d.OWNER_KEY) == owner["owner"]
    }
    projection = (
        '{range .items[*]}{.metadata.uid}{"\\t"}{.metadata.ownerReferences[*].uid}{"\\n"}{end}'
    )
    rows = d.k("get", "replicasets", "-o", "jsonpath=" + projection).stdout
    replicas = set()
    for row in rows.splitlines():
        uid, owners = row.split("\t")
        if any(o in deployments for o in owners.split()):
            replicas.add(uid)
    return {
        p["name"]
        for p in pods
        if p["namespace"] == d.NAMESPACE and any(uid in replicas for uid in p["owners"])
    }


def memory_usage():
    """Metrics API is optional; inability to read it is explicit, never zero usage."""
    d = api()
    result = d.k("get", "--raw=/apis/metrics.k8s.io/v1beta1/pods", check=False)
    if result.returncode:
        return None
    values = json.loads(result.stdout)["items"]
    return {
        (v["metadata"]["namespace"], v["metadata"]["name"]): sum(
            quantity(c["usage"]["memory"]) for c in v["containers"]
        )
        for v in values
    }


def cni_status():
    d = api()
    result = d.run(
        [
            "docker",
            "exec",
            d.TARGET["node_name"],
            "sh",
            "-c",
            "cat /etc/cni/net.d/*.conf /etc/cni/net.d/*.conflist 2>/dev/null",
        ],
        check=False,
    )
    try:
        configs = decode_objects(result.stdout)
    except ValueError:
        configs = []
    types = set()
    for config in configs:
        types.update(p.get("type", "") for p in config.get("plugins", [config]))
    if "calico" in types or "cilium-cni" in types:
        return {"plugins": sorted(types), "policy_support": "capable_not_verified"}
    if "bridge" in types or "kindnet" in types or "flannel" in types:
        return {"plugins": sorted(types), "policy_support": "not_enforced"}
    return {"plugins": sorted(types), "policy_support": "unknown"}


def deployment_requirements(args, owner):
    """Mandatory compatibility checks; never downgraded to diagnostic warnings."""
    d = api()
    arch = d.native_arch(platform.machine())
    nodes = json.loads(d.k("get", "nodes", "-o", "json").stdout)["items"]
    d.require(len(nodes) == 1, "This deployment requires an existing single-node cluster.")
    node = nodes[0]
    d.require(
        arch and node["status"]["nodeInfo"]["architecture"] == arch,
        "Host and Kubernetes node architectures differ; cross-architecture emulation is not "
        "allowed.",
    )
    info = json.loads(d.run(["docker", "info", "--format", "{{json .}}"]).stdout)
    d.require(
        info["OSType"] == "linux" and d.native_arch(info["Architecture"]) == arch,
        "Docker VM architecture must match the native host.",
    )
    sc_name = args.storage_class or (owner or {}).get("storage_class", "standard")
    sc = d.obj("storageclass", sc_name)
    d.require(
        sc["provisioner"] == "k8s.io/minikube-hostpath",
        "Only an existing minikube-hostpath StorageClass is supported; storage components are "
        "not installed or replaced.",
    )
    if owner:
        for name in ("review-history", "review-model-cache", "review-dynamodb"):
            pvc = d.obj("pvc", name, optional=True)
            if pvc:
                d.require(
                    pvc["spec"]["storageClassName"] == sc_name,
                    "The existing PVC StorageClass does not match; PVCs are not modified or "
                    "recreated.",
                )
    return arch, node, info, sc_name


def preflight(args, owner):
    """Collect diagnostics without deciding whether deployment is allowed.

    Only expected measurement/parse failures are downgraded, at the individual read boundary.
    Mandatory target, ownership, architecture and storage checks are outside those boundaries.
    """
    d = api()
    arch, node, info, sc_name = deployment_requirements(args, owner)
    errors = []
    measurements = {}

    def measure(name, read):
        try:
            value = read()
        except (d.DemoError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Never include arbitrary third-party output, credentials, or response bodies.
            reason = str(exc) if isinstance(exc, d.DemoError) else type(exc).__name__
            message = f"{name} was not measured: {reason}."
        else:
            if value is not None:
                measurements[name] = {"status": "measured"}
                return value
            message = f"{name} was not measured: no measurement was available."
        measurements[name] = {"status": "not_measured", "reason": message}
        errors.append(message)
        return None

    def gib(value):
        return round(value / GIB, 2) if value is not None else "not_measured"

    def field(value):
        return value if value is not None else "not_measured"

    if node["spec"].get("unschedulable") or not any(
        c["type"] == "Ready" and c["status"] == "True" for c in node["status"].get("conditions", [])
    ):
        errors.append("The target node is not Ready or is unschedulable; rollout may fail.")
    if any(t["effect"] in {"NoSchedule", "NoExecute"} for t in node["spec"].get("taints", [])):
        errors.append("The target node has an untolerated taint; the script will not modify it.")

    def versions():
        value = json.loads(d.k("version", "-o", "json").stdout)
        client, server = (value[k]["gitVersion"] for k in ("clientVersion", "serverVersion"))
        minors = [int(re.search(r"v1\.(\d+)", v)[1]) for v in (client, server)]
        return client, server, abs(minors[0] - minors[1])

    version = measure("client_server_versions", versions)
    if version is not None and version[2] > 1:
        errors.append(
            "kubectl and the existing API server differ by more than one minor version; "
            "select a compatible client manually."
        )
    cni = measure("cni_configuration", cni_status) or {"plugins": [], "policy_support": "unknown"}
    print("CNI: " + json.dumps(cni, ensure_ascii=False))
    if cni["policy_support"] != "capable_not_verified":
        print(
            "WARNING: Network isolation is not enforced or could not be confirmed. "
            "The CNI will not be installed or modified; acceptance records enforcement separately."
        )
    desired = sum_budgets(
        [
            r["spec"]["template"]["spec"]
            for r in d.render(args.port or 8080)
            if r["kind"] == "Deployment"
        ]
    )
    pods = measure("workload_inventory", workload_inventory)
    own_names = (
        measure("owned_pod_inventory", lambda: owned_pod_names(owner, pods))
        if pods is not None
        else None
    )
    other = (
        measure(
            "other_workload_resources",
            lambda: sum_budgets(
                [
                    p["spec"]
                    for p in pods
                    if p["name"] not in own_names or p["namespace"] != d.NAMESPACE
                ]
            ),
        )
        if pods is not None and own_names is not None
        else None
    )
    usage = measure("pod_metrics", memory_usage)
    own_used = 0 if own_names == set() else None
    if own_names:
        if usage is not None and all((d.NAMESPACE, n) in usage for n in own_names):
            own_used = sum(usage[(d.NAMESPACE, n)] for n in own_names)
        else:

            def own_cgroups():
                samples = []
                for name in own_names:
                    value = d.k(
                        "exec",
                        name,
                        "--",
                        "sh",
                        "-c",
                        "cat /sys/fs/cgroup/memory.current 2>/dev/null || "
                        "cat /sys/fs/cgroup/memory/memory.usage_in_bytes",
                        check=False,
                    )
                    if value.returncode == 0 and value.stdout.strip().isdigit():
                        samples.append(int(value.stdout.strip()))
                return sum(samples) if len(samples) == len(own_names) else None

            own_used = measure("owned_pod_memory", own_cgroups)

    def docker_stats():
        stats = d.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"]
        )
        rows = [line.split("\t") for line in stats.stdout.splitlines()]
        measured = {row[0]: docker_usage(row[1]) for row in rows}
        cpus = {row[0]: float(row[2].rstrip("%")) / 100 for row in rows}
        d.require(d.TARGET["node_name"] in measured, "Target Docker node usage was not measured.")
        return measured, cpus

    stats = measure("docker_usage", docker_stats)
    measured, cpu_observed = stats if stats is not None else ({}, {})
    node_used = measured.get(d.TARGET["node_name"])
    docker_total = measure("docker_memory_quota", lambda: int(info["MemTotal"]))
    docker_cpu = measure("docker_cpu_quota", lambda: int(info["NCPU"]))

    def node_limits():
        template = (
            '{"memory":{{.HostConfig.Memory}},"nano":{{.HostConfig.NanoCpus}},'
            '"quota":{{.HostConfig.CpuQuota}},"period":{{.HostConfig.CpuPeriod}},'
            '"cpuset":{{json .HostConfig.CpusetCpus}}}'
        )
        limits = json.loads(
            d.run(["docker", "inspect", "--format", template, d.TARGET["node_name"]]).stdout
        )
        return limits["memory"] or docker_total, docker_cpu_limit(limits, docker_cpu)

    limits = measure("node_quotas", node_limits)
    cap, cpu_cap = limits if limits is not None else (None, None)
    host = measure("host_memory", d.host_memory)
    host_total, host_free = host if host is not None else (None, None)
    alloc = measure(
        "node_allocatable",
        lambda: {r: quantity(node["status"]["allocatable"][r]) for r in ("cpu", "memory")},
    )
    # Build inputs are deployment requirements, not optional resource measurements.
    fingerprints = {f"review-{c}": source_fingerprint(c) for c in ("backend", "frontend")}
    reusable = measure("reusable_images", lambda: reusable_images(owner, fingerprints, arch))
    # Unknown cache availability gets the full disk budget, never an assumed cache deduction.
    reusable = reusable if reusable is not None else {}
    # Per changed image: layers/copies 4 GiB + build scratch 3 GiB.
    # 10 GiB covers transfer scratch, tokenizer assets and safety/data growth.
    # Ollama weights live in its independently managed service, not the backend cache.
    disk_need = (10 + 7 * (2 - len(reusable))) * GIB
    disk_free = measure("host_disk", lambda: shutil.disk_usage(d.ROOT).free)

    def vm_disk():
        result = d.run(["docker", "exec", d.TARGET["node_name"], "df", "-Pk", "/var/lib"])
        return int(result.stdout.splitlines()[-1].split()[3]) * 1024

    vm_free = measure("vm_disk", vm_disk)
    if vm_free is not None and vm_free < disk_need:
        errors.append(
            "Actual free space on the Docker virtual disk is insufficient for the incremental "
            "application budget."
        )
    errors += capacity_errors(
        desired=desired,
        other=other,
        allocatable=alloc,
        host_available=host_free,
        docker_total=docker_total,
        docker_used=sum(measured.values()) if stats is not None else None,
        node_used=node_used,
        node_cap=cap,
        own_used=own_used,
        disk_free=disk_free,
        disk_need=disk_need,
        node_cpu_cap=cpu_cap,
    )
    report = {
        "profile": d.PROFILE,
        "cluster_uid": d.TARGET["cluster_uid"],
        "status": "failed" if errors else "passed",
        "measurements": measurements,
        "host_total_gib": gib(host_total),
        "host_available_estimate_gib": gib(host_free),
        "docker_quota_gib": gib(docker_total),
        "docker_cpu_quota": field(docker_cpu),
        "docker_observed_cpu_cores": (
            round(sum(cpu_observed.values()), 2) if stats is not None else "not_measured"
        ),
        "node_observed_cpu_cores": field(cpu_observed.get(d.TARGET["node_name"])),
        "node_cpu_cap": field(cpu_cap),
        "kubectl_version": version[0] if version else "not_measured",
        "api_server_version": version[1] if version else "not_measured",
        "docker_observed_usage_gib": gib(sum(measured.values()) if stats is not None else None),
        "node_allocatable": field(alloc),
        "node_memory_cap_gib": gib(cap),
        "node_observed_usage_gib": gib(node_used),
        "other_workload_resources": field(other),
        "desired_resources": desired,
        "own_usage_bytes": field(own_used),
        "pod_metrics": "available" if usage is not None else "not_measured",
        "host_disk_free_gib": gib(disk_free),
        "vm_disk_free_gib": gib(vm_free),
        "incremental_disk_budget_gib": disk_need / GIB,
        "reusable_images": sorted(reusable),
        "cni": cni,
        "storage_class": sc_name,
        "blockers": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {
        "report": report,
        "fingerprints": fingerprints,
        "reusable_images": reusable,
        "architecture": arch,
        "storage_class": sc_name,
        "cni": cni,
    }
