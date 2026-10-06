"""Select an existing local Minikube profile without changing global kubeconfig."""

import base64
import contextlib
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def run(*args, data=None, check=True, timeout=120, env=None):
    result = subprocess.run(
        [str(a) for a in args],
        input=data,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"{args[0]} {args[1]} failed (exit {result.returncode}); output withheld."
        )
    return result


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_host(value):
    address = ipaddress.IPv4Address(value)
    require(
        any(
            address in ipaddress.ip_network(n)
            for n in (
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
            )
        ),
        "Ollama host must resolve to one private IPv4 address",
    )
    return str(address)


def quantity(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]*)", str(value))
    require(match is not None, "Unsupported resource quantity")
    scale = {
        "": 1,
        "m": 0.001,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
    }
    require(match[2] in scale, "Unsupported resource unit")
    return float(match[1]) * scale[match[2]]


def pod_requests(spec, resource):
    def request(container):
        return quantity(container.get("resources", {}).get("requests", {}).get(resource, "0"))

    regular = sum(request(c) for c in spec.get("containers", []))
    initial = max((request(c) for c in spec.get("initContainers") or []), default=0)
    return max(regular, initial) + quantity(spec.get("overhead", {}).get(resource, "0"))


class Target:
    def __init__(self, profile, namespace, kubeconfig, node, env):
        self.profile, self.namespace = profile, namespace
        self.kubeconfig, self.node, self.env = kubeconfig, node, env
        self.kube = [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "--context",
            profile,
            "-n",
            namespace,
        ]
        self.helm = [
            "helm",
            "--kubeconfig",
            str(kubeconfig),
            "--kube-context",
            profile,
            "--namespace",
            namespace,
        ]

    def kubectl(self, *args, **kwargs):
        return run(*self.kube, *args, env=self.env, **kwargs)

    def object(self, kind, name):
        result = self.kubectl("get", kind, name, "--ignore-not-found", "-o", "json")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def ollama_ip(self):
        output = run(
            "docker",
            "exec",
            self.node,
            "getent",
            "ahostsv4",
            "host.minikube.internal",
            timeout=15,
        ).stdout
        addresses = {private_host(line.split()[0]) for line in output.splitlines() if line.strip()}
        require(len(addresses) == 1, "Ollama host resolution is missing or ambiguous")
        return addresses.pop()


@contextlib.contextmanager
def connect(profile, namespace, minikube_home=None):
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", profile), "Invalid profile")
    require(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", namespace),
        "Invalid namespace",
    )
    home = (
        Path(minikube_home or os.environ.get("MINIKUBE_HOME", "~/.minikube")).expanduser().resolve()
    )
    if home.name != ".minikube":
        home /= ".minikube"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "HF_", "HELM_KUBE"))}
    env.update(
        MINIKUBE_HOME=str(home),
        AWS_CONFIG_FILE="/dev/null",
        AWS_SHARED_CREDENTIALS_FILE="/dev/null",
        AWS_EC2_METADATA_DISABLED="true",
    )
    docker = json.loads(run("docker", "context", "inspect").stdout)[0]
    require(
        docker["Endpoints"]["docker"]["Host"].startswith("unix://"),
        "Docker must use a local Unix socket",
    )
    require(
        not os.environ.get("DOCKER_HOST") or os.environ["DOCKER_HOST"].startswith("unix://"),
        "Remote DOCKER_HOST is unsupported",
    )
    listing = json.loads(
        run("minikube", "--skip-audit", "profile", "list", "-o", "json", env=env).stdout
    )
    matches = [p for p in listing.get("valid", []) if p["Name"] == profile]
    require(len(matches) == 1, "The requested Minikube profile does not exist")
    config = matches[0]["Config"]
    require(
        config["Driver"] == "docker" and len(config["Nodes"]) == 1,
        "Use an existing single-node Docker Minikube profile",
    )
    state = json.loads(
        run("minikube", "--skip-audit", "-p", profile, "status", "-o", "json", env=env).stdout
    )
    require(
        all(state.get(k) == "Running" for k in ("Host", "APIServer", "Kubelet")),
        "Start the selected Minikube profile first",
    )
    node = config["Nodes"][0].get("Name") or profile
    info = json.loads(run("docker", "container", "inspect", node).stdout)[0]
    require(
        info["Config"]["Labels"].get("name.minikube.sigs.k8s.io") == profile,
        "Docker node/profile mismatch",
    )
    bindings = [
        b
        for b in info["NetworkSettings"]["Ports"].get("8443/tcp", [])
        if b["HostIp"] == "127.0.0.1"
    ]
    require(len(bindings) == 1, "Expected one loopback Kubernetes API binding")
    ca, cert, key = [
        base64.b64encode(p.read_bytes()).decode()
        for p in (
            home / "ca.crt",
            home / "profiles" / profile / "client.crt",
            home / "profiles" / profile / "client.key",
        )
    ]
    with tempfile.TemporaryDirectory(prefix="review-helm-") as scratch:
        kubeconfig = Path(scratch) / "kubeconfig"
        kubeconfig.write_text(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Config",
                    "current-context": profile,
                    "clusters": [
                        {
                            "name": profile,
                            "cluster": {
                                "server": f"https://127.0.0.1:{int(bindings[0]['HostPort'])}",
                                "certificate-authority-data": ca,
                            },
                        }
                    ],
                    "users": [
                        {
                            "name": profile,
                            "user": {
                                "client-certificate-data": cert,
                                "client-key-data": key,
                            },
                        }
                    ],
                    "contexts": [
                        {
                            "name": profile,
                            "context": {
                                "cluster": profile,
                                "user": profile,
                                "namespace": namespace,
                            },
                        }
                    ],
                }
            )
        )
        kubeconfig.chmod(0o600)
        target = Target(profile, namespace, kubeconfig, node, env)
        require(
            target.kubectl("get", "--raw=/readyz", "--request-timeout=10s").stdout.strip() == "ok",
            "Kubernetes API not ready",
        )
        target.cluster_uid = target.object("namespace", "kube-system")["metadata"]["uid"]
        print(f"Target: profile={profile}, namespace={namespace}, cluster UID={target.cluster_uid}")
        yield target


def check_ownership(resource, release, namespace):
    metadata = resource["metadata"]
    annotations = metadata.get("annotations", {})
    require(
        annotations.get("meta.helm.sh/release-name") == release
        and annotations.get("meta.helm.sh/release-namespace") == namespace
        and metadata.get("labels", {}).get("app.kubernetes.io/managed-by") == "Helm",
        f"Existing {resource['kind']}/{metadata['name']} is not owned by this Helm release. "
        "Follow the Kustomize migration guide; automatic takeover is disabled.",
    )
