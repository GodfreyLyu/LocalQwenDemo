"""Local image builds and standard Helm lifecycle for an existing Minikube cluster.

Helm owns application resources. Local records are build/acceptance evidence, never
an alternative release database. Use the explicit `legacy` entry for Kustomize recovery.
"""

import contextlib
import hashlib
import json
import os
import secrets
import shlex
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from tooling_paths import ROOT

from deployment.common.files import atomic_json, exclusive_lock
from deployment.common.forward import forward
from deployment.common.target import check_ownership, require, run
from deployment.helm.build import build_images
from deployment.helm.constants import BOOTSTRAP, COMPONENTS
from deployment.helm.diagnostics import doctor


def atomic(path, value):
    atomic_json(path, value, create_parent=True)


CHART = ROOT / "deploy/helm/local-review"


def merge(left, right):
    result = dict(left)
    for key, value in right.items():
        result[key] = (
            merge(result.get(key, {}), value)
            if isinstance(value, dict) and isinstance(result.get(key, {}), dict)
            else value
        )
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Session:
    def __init__(self, target, args):
        self.target, self.args = target, args
        self.release, self.namespace = args.release, args.namespace
        root = (
            Path(
                args.state_root
                or os.environ.get("LOCAL_QWEN_STATE_HOME")
                or (
                    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                    / "local-qwen-demo"
                )
            )
            .expanduser()
            .resolve()
        )
        identity = [
            target.cluster_uid,
            target.profile,
            target.env.get("MINIKUBE_HOME"),
            self.namespace,
            self.release,
        ]
        self.directory = root / "helm-v1" / digest(identity)
        self.namespace_uid = None

    def save(self, name, value):
        atomic(self.directory / name, value)

    def read(self, name):
        path = self.directory / name
        return json.loads(path.read_text()) if path.exists() else {}

    @contextlib.contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / "operation.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            with exclusive_lock(lock):
                yield

    def ns(self):
        ns = self.target.object("namespace", self.namespace)
        current = ns["metadata"]["uid"] if ns else None
        if self.namespace_uid is not None:
            require(current == self.namespace_uid, "Namespace identity changed during operation.")
        self.namespace_uid = current
        return ns

    def guard(self):
        require(
            self.target.object("namespace", "kube-system")["metadata"]["uid"]
            == self.target.cluster_uid,
            "Cluster identity changed.",
        )
        return self.ns()

    def archive_stale(self):
        ns = self.ns()
        identity = {"namespace_uid": ns["metadata"]["uid"] if ns else None}
        previous = self.read("target.json")
        if previous and previous != identity:
            archive = self.directory / ("archive-" + str(time.time_ns()))
            archive.mkdir(mode=0o700)
            for path in self.directory.glob("*.json"):
                path.rename(archive / path.name)
            print("Archived local evidence for the previous namespace identity.")
        self.save("target.json", identity)

    def helm(self, *args, **kwargs):
        return run(*self.target.helm, *args, env=self.target.env, **kwargs)

    def release_info(self):
        # Explicit status filters work on both Helm 3 and Helm 4.
        rows = json.loads(
            self.helm(
                "list",
                "--deployed",
                "--failed",
                "--pending",
                "--uninstalled",
                "--uninstalling",
                "--superseded",
                "--filter",
                "^" + self.release + "$",
                "-o",
                "json",
            ).stdout
        )
        require(len(rows) <= 1, "Ambiguous Helm release.")
        if not rows:
            return None
        return json.loads(self.helm("status", self.release, "-o", "json").stdout)

    def release_manifest(self, revision=None):
        args = ["get", "manifest", self.release]
        if revision is not None:
            args += ["--revision", str(revision)]
        return [r for r in yaml.safe_load_all(self.helm(*args).stdout) if r]

    def validate_release(self, info, *, allow_pending=False):
        require(info is not None, "Helm release not found; install with up or standard Helm first.")
        require(
            info.get("chart", {}).get("metadata", {}).get("name") == "local-review",
            "This release is not the local-review Chart.",
        )
        require(
            allow_pending or not info["info"]["status"].startswith("pending-"),
            "Helm operation is pending; inspect helm status/history before retrying.",
        )
        for resource in self.release_manifest():
            require(
                resource["kind"]
                in {
                    "Deployment",
                    "Service",
                    "ConfigMap",
                    "ServiceAccount",
                    "PersistentVolumeClaim",
                    "NetworkPolicy",
                },
                "Unexpected release resource kind.",
            )
            require(
                resource["metadata"].get("namespace", self.namespace) == self.namespace,
                "Release contains resources outside the selected namespace.",
            )
            live = self.target.object(resource["kind"], resource["metadata"]["name"])
            if live:
                check_ownership(live, self.release, self.namespace)

    def evidence(self):
        info = self.release_info()
        self.validate_release(info)
        require(info["info"]["status"] == "deployed", "Release is not deployed.")
        pods = json.loads(
            self.target.kubectl(
                "get", "pods", "-l", "app.kubernetes.io/instance=" + self.release, "-o", "json"
            ).stdout
        )["items"]
        require(
            len(pods) == 3
            and all(
                any(
                    c["type"] == "Ready" and c["status"] == "True"
                    for c in p.get("status", {}).get("conditions", [])
                )
                for p in pods
            ),
            "Expected three ready application Pods.",
        )
        images = [
            list(item)
            for item in sorted(
                {
                    (c["name"], c.get("imageID", ""))
                    for p in pods
                    for c in p.get("status", {}).get("containerStatuses", [])
                }
            )
        ]
        require(
            len(images) == 3 and all(i for _, i in images), "Runtime image identity unavailable."
        )
        values = json.loads(self.helm("get", "values", self.release, "--all", "-o", "json").stdout)
        return {
            "cluster_uid": self.target.cluster_uid,
            "namespace_uid": self.ns()["metadata"]["uid"],
            "release": self.release,
            "revision": info["version"],
            "config_digest": digest(values),
            "runtime_images": images,
        }

    def config(self):
        self.validate_release(self.release_info())
        resource = self.target.object("configmap", "review-config")
        require(resource is not None, "Release ConfigMap is missing.")
        check_ownership(resource, self.release, self.namespace)
        return resource["data"]

    def origin(self):
        origin = self.config()["ALLOWED_ORIGIN"]
        url = urlsplit(origin)
        require(
            url.scheme == "http"
            and url.hostname == "localhost"
            and url.port is not None
            and not url.path
            and not url.username
            and not url.query
            and not url.fragment,
            "Local forwarding requires ALLOWED_ORIGIN=http://localhost:PORT.",
        )
        require(
            self.args.port in (None, url.port), "Port differs from deployed Origin; upgrade first."
        )
        return origin, url.port

    def forward(self, port, wait=False):
        return forward(
            "review-frontend",
            8080,
            port,
            wait=wait,
            command=lambda *a: [*self.target.kube, *a],
            env=self.target.env,
        )

    def options(self):
        values = yaml.safe_load((CHART / "values.yaml").read_text())
        values = merge(values, yaml.safe_load((CHART / "values-minikube.yaml").read_text()))
        for path in self.args.values:
            override = yaml.safe_load(Path(path).expanduser().read_text())
            require(isinstance(override, dict), "Each values file must contain a mapping.")
            values = merge(values, override)
        if self.args.port is not None:
            values = merge(
                values, {"config": {"allowedOrigin": f"http://localhost:{self.args.port}"}}
            )
        if self.args.storage_class:
            values = merge(values, {"persistence": {"storageClass": self.args.storage_class}})
        return values

    def render(self, values):
        # Diagnostic renders must never replace the last reproducible deployment file.
        with tempfile.TemporaryDirectory(prefix="review-values-") as directory:
            path = Path(directory) / "values.json"
            atomic(path, values)
            result = run(
                "helm",
                "template",
                self.release,
                CHART,
                "--namespace",
                self.namespace,
                "-f",
                path,
                env=self.target.env,
            )
        return [r for r in yaml.safe_load_all(result.stdout) if r]

    def secret_metadata(self, name):
        output = self.target.kubectl(
            "get", "secret", name, "--ignore-not-found", "-o", "jsonpath={.metadata}"
        ).stdout
        return json.loads(output) if output.strip() else None

    def check_secret(self, name):
        valid = self.target.kubectl(
            "get",
            "secret",
            name,
            "--ignore-not-found",
            "-o",
            "go-template={{if .data.SIGNING_SECRET}}{{ge (len (.data.SIGNING_SECRET | "
            "base64decode)) 32}}{{else}}false{{end}}",
        ).stdout.strip()
        require(
            valid == "true", "Missing/invalid signing Secret; run init or prepare it with kubectl."
        )

    def init(self):
        values = self.options()
        self.render(values)  # Reject invalid configuration before creating resources.
        ns = self.guard()
        if ns:
            require(
                not ns["metadata"].get("annotations", {}).get("local-review-demo/owner"),
                "Legacy namespace; use legacy migration/recovery, not automatic takeover.",
            )
        else:
            self.target.kubectl(
                "create",
                "-f",
                "-",
                data=json.dumps(
                    {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {
                            "name": self.namespace,
                            "annotations": {BOOTSTRAP: self.release},
                        },
                    }
                ),
            )
            self.namespace_uid = None
            self.archive_stale()
        name = values["signingSecret"]["existingSecret"]
        if not self.secret_metadata(name):
            self.target.kubectl(
                "create",
                "-f",
                "-",
                data=json.dumps(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "type": "Opaque",
                        "metadata": {
                            "name": name,
                            "namespace": self.namespace,
                            "annotations": {BOOTSTRAP: self.release},
                        },
                        "stringData": {"SIGNING_SECRET": secrets.token_urlsafe(48)},
                    }
                ),
            )
        self.check_secret(name)
        print("Namespace and stable signing Secret ready. No application installed.")

    def preflight(self, values, resources):
        require(self.guard() is not None, "Namespace missing; run init or prepare it with kubectl.")
        self.check_secret(values["signingSecret"]["existingSecret"])
        for secret in values["imagePullSecrets"]:
            require(self.secret_metadata(secret["name"]), "Image pull Secret is missing.")
        for resource in resources:
            live = self.target.object(resource["kind"], resource["metadata"]["name"])
            if live:
                check_ownership(live, self.release, self.namespace)
        generated = {
            r["metadata"]["name"] for r in resources if r["kind"] == "PersistentVolumeClaim"
        }
        for key in ("history", "modelCache", "dynamodb"):
            name = values["persistence"][key]["existingClaim"]
            if name:
                pvc = self.target.object("pvc", name)
                require(
                    pvc and pvc["status"]["phase"] == "Bound", f"External PVC {name} is not Bound."
                )
        if generated:
            sc = self.target.object("storageclass", values["persistence"]["storageClass"])
            require(
                sc and sc["provisioner"] == "k8s.io/minikube-hostpath",
                "Minikube StorageClass missing.",
            )

    def doctor(self, values, resources):
        return doctor(self, values, resources)

    def build(self, arch):
        return build_images(self, arch)

    def up(self):
        from deployment.helm.lifecycle import quiesced

        values = self.options()
        resources = self.render(values)
        self.preflight(values, resources)
        info = self.release_info()
        if info:
            self.validate_release(info)
        report = self.doctor(values, resources)
        values = merge(values, self.build(report["architecture"]))
        resources = self.render(values)
        self.preflight(values, resources)
        path = self.directory / "values-local.json"
        self.save(path.name, values)
        run("helm", "lint", CHART, "-f", path, env=self.target.env)
        command = [
            "upgrade",
            "--install",
            self.release,
            str(CHART),
            "--reset-values",
            "-f",
            str(path),
            "--wait",
            "--timeout",
            f"{self.args.timeout}s",
        ]
        print("Values: " + str(path))
        print(
            "Equivalent command: "
            + shlex.join(
                [
                    "helm",
                    "--kube-context",
                    self.target.profile,
                    "--namespace",
                    self.namespace,
                    *command,
                ]
            )
        )
        with quiesced(self, restore=True):
            self.guard()
            self.helm(*command, timeout=self.args.timeout + 30)
        self.wait_ready()
        self.save("deployment.json", self.evidence())
        print("Helm deployment ready. Run verify for real inference and persistence acceptance.")

    def wait_ready(self):
        for name in COMPONENTS:
            self.target.kubectl(
                "rollout",
                "status",
                "deployment/review-" + name,
                f"--timeout={self.args.timeout}s",
                timeout=self.args.timeout + 30,
            )

    def rollback(self):
        from deployment.helm.lifecycle import quiesced

        self.validate_release(self.release_info())
        history = json.loads(self.helm("history", self.release, "-o", "json").stdout)
        require(
            any(int(r["revision"]) == self.args.revision for r in history), "Revision not found."
        )
        # Target revisions may refer to unavailable local images or external data.
        resources = self.release_manifest(self.args.revision)
        values = json.loads(
            self.helm(
                "get",
                "values",
                self.release,
                "--all",
                "--revision",
                str(self.args.revision),
                "-o",
                "json",
            ).stdout
        )
        self.preflight(values, resources)
        with quiesced(self, restore=True):
            self.helm(
                "rollback",
                self.release,
                str(self.args.revision),
                "--wait",
                "--timeout",
                f"{self.args.timeout}s",
                timeout=self.args.timeout + 30,
            )
        self.wait_ready()
        self.save("deployment.json", self.evidence())
        print("Rollback ready; external Secrets and database contents were not rolled back.")

    def status(self):
        info = self.release_info()
        if not info:
            print("No Helm release found.")
        else:
            self.validate_release(info, allow_pending=True)
            print(
                json.dumps(
                    {
                        "release": self.release,
                        "revision": info["version"],
                        "status": info["info"]["status"],
                    }
                )
            )
            acceptance = self.read("verification.json")
            if acceptance:
                current = None
                if info["info"]["status"] == "deployed":
                    try:
                        current = self.evidence()
                    except ValueError:
                        print("Runtime is not ready; previous acceptance is not current.")
                print(
                    "Acceptance: "
                    + (
                        acceptance["status"]
                        if current is not None and acceptance.get("evidence") == current
                        else "stale (release or runtime changed)"
                    )
                )
        print(self.target.kubectl("get", "pods,pvc").stdout)
