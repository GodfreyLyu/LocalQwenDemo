"""Deterministic release inputs and allowlisted deployment snapshots; no cluster access."""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

CHART = "deploy/helm/local-review"
OLLAMA_CHART = "deploy/helm/local-ollama"
CHARTS = (CHART, OLLAMA_CHART)
COMPONENTS = ("backend", "frontend", "ollama")
DEPLOY_FILES = ()
ARGOCD = "deploy/argocd"
FORMAT = 3
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
    ollama = [p for p in tracked if p.startswith("deploy/images/ollama-vulkan/")]
    deployment = [
        p
        for p in tracked
        if any(p.startswith(c + "/") for c in CHARTS) or p.startswith(ARGOCD + "/")
    ]
    return {
        k: fingerprint(root, paths)
        for k, paths in {
            "backend": backend,
            "frontend": frontend,
            "ollama": ollama,
            "deployment": deployment,
        }.items()
    }


def plan(root, previous, repository):
    source = git(root, "rev-parse", "HEAD")
    assert SHA.fullmatch(source)
    current = inputs(root)
    images = {}
    for component in COMPONENTS:
        old = previous.get("images", {}).get(component, {})
        name = f"ghcr.io/{repository.lower()}-{component}"
        reuse = (
            previous.get("inputs", {}).get(component) == current[component]
            and old.get("repository") == name
            and DIGEST.fullmatch(old.get("digest", ""))
            and SHA.fullmatch(old.get("sourceSha", ""))
        )
        images[component] = (
            copy.deepcopy(old) if reuse else {"repository": name, "sourceSha": source}
        )
    return {
        "schemaVersion": FORMAT,
        "sourceSha": source,
        "repository": repository,
        "inputs": current,
        "images": images,
        "changed": (
            previous.get("schemaVersion") != FORMAT
            or current != previous.get("inputs")
            or images != previous.get("images")
        ),
        "build": {k: "digest" not in v for k, v in images.items()},
    }


def candidate_metadata(root, base, run_number, run_id):
    version = yaml.safe_load((root / CHART / "Chart.yaml").read_text())["version"]
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Source Chart version must be a stable numeric SemVer")
    if not SHA.fullmatch(base):
        raise ValueError("Invalid release base SHA")
    if not re.fullmatch(r"[1-9][0-9]*", str(run_number)) or not re.fullmatch(
        r"[1-9][0-9]*", str(run_id)
    ):
        raise ValueError("Positive GitHub run number and run ID are required")
    version += "-rc." + str(run_number)
    return {
        "version": version,
        "branch": "release-candidate/" + version,
        "runId": str(run_id),
        "runNumber": int(run_number),
        "baseSha": base,
    }


def materialize(root, destination, candidate, digests):
    """Destination is a dedicated candidate checkout; only generated paths are replaced."""
    manifest = copy.deepcopy({k: v for k, v in candidate.items() if k not in {"changed", "build"}})
    for component, entry in manifest["images"].items():
        if candidate["build"][component]:
            entry["digest"] = digests[component]
        if not DIGEST.fullmatch(entry["digest"]):
            raise ValueError("Image digest is missing or invalid")
    destination.mkdir(parents=True, exist_ok=True)
    # A dedicated worktree: remove legacy deployment scripts and workflow entrypoints.
    # The one-time workflow-removal PR must be merged before the bot can publish.
    for path in destination.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    if manifest.get("schemaVersion") != FORMAT or "candidate" not in manifest:
        raise ValueError("New snapshots require format 3 versioned candidate metadata")
    manifest["chartVersions"] = {}
    for name in CHARTS:
        target = destination / name
        shutil.copytree(root / name, target)
        chart = yaml.safe_load((target / "Chart.yaml").read_text())
        # Preserve Ollama's runtime appVersion and independent Chart version: a
        # backend-only change must not restart the Ollama workload.
        if name == CHART:
            chart["version"] = manifest["candidate"]["version"]
            chart["appVersion"] = manifest["sourceSha"]
        manifest["chartVersions"][chart["name"]] = chart["version"]
        (target / "Chart.yaml").write_text(yaml.safe_dump(chart, sort_keys=False))
    manifest["chartVersion"] = manifest["candidate"]["version"]
    shutil.copytree(root / ARGOCD, destination / ARGOCD)
    repo_url = "https://github.com/" + manifest["repository"] + ".git"
    for path in (destination / ARGOCD).glob("*.yaml"):
        path.write_text(
            path.read_text().replace("https://github.com/GodfreyLyu/LocalQwenDemo.git", repo_url)
        )
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
        if c != "ollama"
    }
    values["config"] = {"releaseSha": manifest["sourceSha"]}
    (destination / "release-values.yaml").write_text(yaml.safe_dump(values, sort_keys=False))
    (destination / CHART / "values-release.yaml").write_text(
        yaml.safe_dump(values, sort_keys=False)
    )
    ollama = manifest["images"]["ollama"]
    (destination / OLLAMA_CHART / "values-release.yaml").write_text(
        yaml.safe_dump(
            {
                "image": {
                    "repository": ollama["repository"],
                    "digest": ollama["digest"],
                    "tag": "sha-" + ollama["sourceSha"],
                    "pullPolicy": "IfNotPresent",
                },
            },
            sort_keys=False,
        )
    )
    (destination / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (destination / "README.md").write_text(
        "# Local Qwen deployment release\n\n"
        "Generated from main; edit Chart and configuration in main and review the generated PR.\n\n"
        "Argo CD watches deploy/argocd and the two deploy/helm Charts. "
        "Image provenance is recorded in [release.json](release.json).\n"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "render"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--base-sha")
    parser.add_argument("--run-number", default=os.environ.get("GITHUB_RUN_NUMBER"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backend-digest", default="")
    parser.add_argument("--frontend-digest", default="")
    parser.add_argument("--ollama-digest", default="")
    args = parser.parse_args()
    if args.command == "plan":
        previous = (
            json.loads(args.previous.read_text())
            if args.previous and args.previous.exists()
            else {}
        )
        candidate = plan(args.root, previous, args.repository)
        candidate["schemaVersion"] = FORMAT
        candidate["candidate"] = candidate_metadata(
            args.root, args.base_sha or "", args.run_number, args.run_id
        )
        args.plan.write_text(json.dumps(candidate, indent=2) + "\n")
    else:
        materialize(
            args.root,
            args.output,
            json.loads(args.plan.read_text()),
            {
                "backend": args.backend_digest,
                "frontend": args.frontend_digest,
                "ollama": args.ollama_digest,
            },
        )


if __name__ == "__main__":
    main()
