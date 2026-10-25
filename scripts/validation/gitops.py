"""Validate the actual Argo CD value-file order and GPU verification contract offline."""

import re
import subprocess

import yaml

from validation.monitoring import validate_monitoring
from validation.resources import require, validate_resources


def render_application(root, application):
    spec = application["spec"]
    source = spec["source"]
    chart = root / source["path"]
    command = [
        "helm",
        "template",
        source["helm"]["releaseName"],
        str(chart),
        "--namespace",
        spec["destination"]["namespace"],
        "--skip-tests",
    ]
    for value in source["helm"]["valueFiles"]:
        path = chart / value
        require(path.resolve().is_relative_to(chart.resolve()), "Values must stay inside Chart")
        command += ["-f", str(path)]
    return list(yaml.safe_load_all(subprocess.check_output(command, text=True)))


def validate_snapshot(root, manifest):
    repo = "https://github.com/" + manifest["repository"] + ".git"
    project = yaml.safe_load((root / "deploy/argocd/project.yaml").read_text())
    require(
        project["apiVersion"] == "argoproj.io/v1alpha1"
        and project["kind"] == "AppProject"
        and project["metadata"]["name"] == "local-review"
        and project["metadata"]["namespace"] == "argocd",
        "Unexpected AppProject identity",
    )
    require(project["spec"]["sourceRepos"] == [repo], "Unexpected project source")
    require(project["spec"]["clusterResourceWhitelist"] == [], "No cluster-wide app access")
    expected = {
        "review-ollama": (
            "local-ollama",
            "local-inference",
            ["values-release.yaml", "values-argocd.yaml"],
        ),
        "local-review": (
            "local-review",
            "local-review-demo",
            [
                "values-release.yaml",
                "values-minikube.yaml",
                "values-minikube-ollama.yaml",
                "values-monitoring.yaml",
            ],
        ),
    }
    allowed = {
        ("", "Service"),
        ("", "ConfigMap"),
        ("", "PersistentVolumeClaim"),
        ("", "ServiceAccount"),
        ("apps", "Deployment"),
        ("batch", "Job"),
        ("networking.k8s.io", "NetworkPolicy"),
        ("monitoring.coreos.com", "ServiceMonitor"),
        ("monitoring.coreos.com", "PrometheusRule"),
    }
    require(
        sorted(project["spec"]["namespaceResourceWhitelist"], key=lambda item: item["kind"])
        == sorted(
            [{"group": group, "kind": kind} for group, kind in allowed],
            key=lambda item: item["kind"],
        ),
        "AppProject must allow only the required application resource kinds",
    )
    require(
        sorted(project["spec"]["destinations"], key=lambda item: item["namespace"])
        == sorted(
            [
                {"server": "https://kubernetes.default.svc", "namespace": namespace}
                for _, namespace, _ in expected.values()
            ],
            key=lambda item: item["namespace"],
        ),
        "AppProject must target only the two application namespaces",
    )
    rendered = {}
    for name, (chart, namespace, values) in expected.items():
        app = yaml.safe_load((root / f"deploy/argocd/{name}-application.yaml").read_text())
        require(
            app["apiVersion"] == "argoproj.io/v1alpha1"
            and app["kind"] == "Application"
            and app["metadata"]["name"] == name
            and app["metadata"]["namespace"] == "argocd",
            "Unexpected Application identity",
        )
        spec = app["spec"]
        require(spec["project"] == "local-review", "Unexpected Argo project")
        require(
            spec["source"]
            == {
                "repoURL": repo,
                "targetRevision": "deployment-release",
                "path": "deploy/helm/" + chart,
                "helm": {"releaseName": name, "skipTests": True, "valueFiles": values},
            },
            "Unexpected Argo source or value precedence",
        )
        require(
            spec["destination"]
            == {
                "server": "https://kubernetes.default.svc",
                "namespace": namespace,
            },
            "Unexpected Argo destination",
        )
        require(
            spec["syncPolicy"]["automated"]
            == {
                "prune": True,
                "selfHeal": True,
                "allowEmpty": False,
            },
            "Unexpected sync policy",
        )
        docs = render_application(root, app)
        rendered[name] = {(r["kind"], r["metadata"]["name"]): r for r in docs}
        require(len(rendered[name]) == len(docs), "Duplicate Kubernetes resource")
        for resource in docs:
            group = resource["apiVersion"].split("/")[0] if "/" in resource["apiVersion"] else ""
            require((group, resource["kind"]) in allowed, "Resource is not allowed by AppProject")
            require(
                resource["metadata"].get("namespace") == namespace,
                "Resource must stay in its Application namespace",
            )
            if resource["kind"] == "PersistentVolumeClaim":
                require(
                    resource["metadata"]["annotations"].get("argocd.argoproj.io/sync-options")
                    == "Prune=false,Delete=false",
                    "Argo PVC retention missing",
                )
        chart_data = yaml.safe_load((root / f"deploy/helm/{chart}/Chart.yaml").read_text())
        require(
            chart_data["version"] == manifest["chartVersions"][chart],
            "Chart version does not match provenance",
        )
    app = validate_resources(list(rendered["local-review"].values()))
    validate_monitoring(list(app.values()))
    # The compatibility root values must agree with the file Argo actually reads.
    require(
        (root / "release-values.yaml").read_bytes()
        == (root / "deploy/helm/local-review/values-release.yaml").read_bytes(),
        "Release values differ from Argo values",
    )
    for component in ("backend", "frontend", "ollama"):
        objects = rendered["review-ollama"] if component == "ollama" else app
        container = objects["Deployment", "review-" + component]["spec"]["template"]["spec"][
            "containers"
        ][0]
        entry = manifest["images"][component]
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", entry["digest"]), "Invalid image digest")
        require(re.fullmatch(r"[a-f0-9]{40}", entry["sourceSha"]), "Invalid image source")
        require(
            entry["repository"] == "ghcr.io/" + manifest["repository"].lower() + "-" + component,
            "Unexpected image repository",
        )
        require(
            container["image"] == entry["repository"] + "@" + entry["digest"],
            "Argo deployment image does not match provenance",
        )
    config = app["ConfigMap", "review-config"]["data"]
    generic = "THRIFT_HOST" in config
    require(
        config.get("THRIFT_HOST") == "review-ollama.local-inference.svc.cluster.local"
        if generic
        else config["OLLAMA_BASE_URL"]
        == "http://review-ollama.local-inference.svc.cluster.local:11434",
        "Backend must use the cluster inference Service",
    )
    ollama = rendered["review-ollama"]
    native_config = ollama["ConfigMap", "review-ollama-config"]["data"]
    if generic:
        for application_key, service_key in (
            ("EXPECTED_MODEL_ID", "MODEL_NAME"),
            ("EXPECTED_MODEL_REVISION", "MODEL_DIGEST"),
        ):
            require(
                not config.get(application_key)
                or config[application_key] == native_config[service_key],
                "Application model assertion differs from the inference model",
            )
    if not generic and native_config["MODEL_BOOTSTRAP"] == "false":
        require(
            ("Job", "review-ollama-gpu-verify") not in ollama, "Bootstrap hook must be disabled"
        )
        return
    require(("Job", "review-ollama-gpu-verify") in ollama, "GPU verification Job missing")
    job = ollama["Job", "review-ollama-gpu-verify"]
    require(
        job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PostSync",
        "GPU verification must gate sync completion",
    )
    test_container = job["spec"]["template"]["spec"]["containers"][0]
    require(
        test_container["image"]
        == manifest["images"]["ollama"]["repository"]
        + "@"
        + manifest["images"]["ollama"]["digest"],
        "GPU test image must match runtime",
    )
    if generic:
        require(
            test_container["command"] == ["python3", "-m", "llm_service.verify"]
            and test_container["args"] == ["--host", "review-ollama"],
            "GPU verification must use the Thrift Service",
        )
        runtime = ollama["Deployment", "review-ollama"]["spec"]["template"]["spec"]
        ports = ollama["Service", "review-ollama"]["spec"]["ports"]
        require(
            {p["port"] for p in ports} == {9091, 11435}, "Only Thrift and health may be exposed"
        )
        require(
            not any("secretRef" in entry for entry in runtime["containers"][0]["envFrom"]),
            "Inference must not receive application authentication secrets",
        )
        model_volume = next(v for v in runtime["volumes"] if v["name"] == "models")
        require(
            bool(model_volume["persistentVolumeClaim"]["claimName"])
            and {m["mountPath"] for m in runtime["containers"][0]["volumeMounts"]}
            == {"/models", "/tmp"},
            "Inference must retain its separate model volume",
        )
    else:
        require(
            test_container["args"] == ["verify", "--url", "http://review-ollama:11434"],
            "GPU verification must use the Service",
        )
    require(
        "devic.es/dri" not in test_container["resources"]["requests"],
        "API verification must not compete for the GPU slot",
    )
