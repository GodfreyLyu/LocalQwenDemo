"""Published snapshot provenance, allowlists and GitOps composition."""

import json
import re

import yaml

from validation.constants import CHART
from validation.resources import require


def validate_tree(root):
    manifest = json.loads((root / "release.json").read_text())
    modern = manifest.get("schemaVersion") == 3
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
    if modern:
        allowed = {"README.md", "release.json", "release-values.yaml"}
        allowed.update(
            {
                "deploy/argocd/" + name
                for name in (
                    "project.yaml",
                    "review-ollama-application.yaml",
                    "local-review-application.yaml",
                )
            }
        )
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        require(not path.is_symlink(), "Release snapshots must not contain symlinks")
        if path.is_file():
            require(
                relative in allowed
                or relative.startswith(CHART.as_posix() + "/")
                or (modern and relative.startswith("deploy/helm/local-ollama/")),
                f"Unexpected release file: {relative}",
            )


def validate_snapshot(root, keyed):
    manifest = json.loads((root / "release.json").read_text())
    require(manifest["schemaVersion"] in {1, 2, 3}, "Unknown release manifest format")
    if manifest["schemaVersion"] in {2, 3}:
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
    if manifest["schemaVersion"] == 3:
        from validation.gitops import validate_snapshot as validate_gitops_snapshot

        validate_gitops_snapshot(root, manifest)
    for directory in ("backend", "frontend"):
        require(
            not (root / directory).exists(),
            "Source code must not be copied into release branch",
        )
