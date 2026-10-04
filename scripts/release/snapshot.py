"""Deterministic release inputs and allowlisted deployment snapshots; no cluster access."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml

CHART = "deploy/helm/local-review"
DEPLOY_FILES = (
    "scripts/helm_deploy.py",
    "scripts/helm_target.py",
    "scripts/validate_helm.py",
    "docs/guides/helm-release.md",
)
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def fingerprint(root, paths):
    digest = hashlib.sha256()
    for relative in sorted(paths):
        path = root / relative
        if path.is_symlink():
            raise ValueError(f"Symlink is not a release input: {relative}")
        digest.update(relative.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def inputs(root):
    tracked = git(root, "ls-files", "-z").split("\0")
    backend = [
        p
        for p in tracked
        if p.startswith("backend/app/")
        or p
        in {
            "backend/Dockerfile",
            "backend/.dockerignore",
            "backend/pyproject.toml",
            "backend/requirements.lock",
            "backend/requirements-model.lock",
        }
    ]
    frontend = [p for p in tracked if p.startswith("frontend/")]
    deployment = [p for p in tracked if p.startswith(CHART + "/") or p in DEPLOY_FILES]
    return {
        k: fingerprint(root, paths)
        for k, paths in {
            "backend": backend,
            "frontend": frontend,
            "deployment": deployment,
        }.items()
    }


def plan(root, previous, repository):
    source = git(root, "rev-parse", "HEAD")
    assert SHA.fullmatch(source)
    current = inputs(root)
    images = {}
    for component in ("backend", "frontend"):
        old = previous.get("images", {}).get(component, {})
        name = f"ghcr.io/{repository.lower()}-{component}"
        reuse = (
            previous.get("inputs", {}).get(component) == current[component]
            and old.get("repository") == name
            and DIGEST.fullmatch(old.get("digest", ""))
            and SHA.fullmatch(old.get("sourceSha", ""))
        )
        images[component] = old if reuse else {"repository": name, "sourceSha": source}
    return {
        "schemaVersion": 1,
        "sourceSha": source,
        "inputs": current,
        "images": images,
        "changed": current != previous.get("inputs") or images != previous.get("images"),
        "build": {k: "digest" not in v for k, v in images.items()},
    }


def materialize(root, destination, candidate, digests):
    """Destination is a dedicated candidate checkout; only generated paths are replaced."""
    manifest = {k: v for k, v in candidate.items() if k not in {"changed", "build"}}
    for component, entry in manifest["images"].items():
        if candidate["build"][component]:
            entry["digest"] = digests[component]
        if not DIGEST.fullmatch(entry["digest"]):
            raise ValueError("Image digest is missing or invalid")
    destination.mkdir(parents=True, exist_ok=True)
    target_chart = destination / CHART
    if target_chart.exists():
        shutil.rmtree(target_chart)
    shutil.copytree(root / CHART, target_chart)
    for name in DEPLOY_FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    chart = yaml.safe_load((target_chart / "Chart.yaml").read_text())
    # A stable SemVer prerelease identifies the exact main commit, including retries.
    chart["version"] = chart["version"].split("-")[0] + "-sha." + manifest["sourceSha"][:12]
    chart["appVersion"] = manifest["sourceSha"]
    (target_chart / "Chart.yaml").write_text(yaml.safe_dump(chart, sort_keys=False))
    manifest["chartVersion"] = chart["version"]
    values = {
        c: {
            "image": {
                "repository": v["repository"],
                "digest": v["digest"],
                "tag": "sha-" + v["sourceSha"],
                "pullPolicy": "IfNotPresent",
            }
        }
        for c, v in manifest["images"].items()
    }
    values["config"] = {"releaseSha": manifest["sourceSha"]}
    (destination / "release-values.yaml").write_text(yaml.safe_dump(values, sort_keys=False))
    (destination / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (destination / "README.md").write_text(
        "# Local Qwen deployment release\n\n"
        "Generated from main; edit Chart and configuration in main and review the generated PR.\n\n"
        "See [deployment instructions](docs/guides/helm-release.md). "
        "Image provenance is recorded in [release.json](release.json).\n"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "render"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backend-digest", default="")
    parser.add_argument("--frontend-digest", default="")
    args = parser.parse_args()
    if args.command == "plan":
        previous = (
            json.loads(args.previous.read_text())
            if args.previous and args.previous.exists()
            else {}
        )
        args.plan.write_text(
            json.dumps(plan(args.root, previous, args.repository), indent=2) + "\n"
        )
    else:
        materialize(
            args.root,
            args.output,
            json.loads(args.plan.read_text()),
            {
                "backend": args.backend_digest,
                "frontend": args.frontend_digest,
            },
        )


if __name__ == "__main__":
    main()
