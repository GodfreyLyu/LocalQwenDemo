"""Legacy deployment policy; no CLI imports or target discovery on import."""

import hashlib
import os
from pathlib import Path

from deployment.legacy.context import api

GIB = 1024**3


def source_fingerprint(context):
    """Hash actual Docker build inputs to prevent stale images from qualifying for reuse."""
    d = api()
    d.require(
        context in ("backend", "frontend"), "Invalid build context; expected backend or frontend."
    )
    d.require(
        (d.ROOT / context).is_dir(),
        f"Build context directory is missing or not a directory: {context}.",
    )
    digest = hashlib.sha256()
    excluded = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "__pycache__",
        "playwright-report",
        "test-results",
        ".pytest_cache",
        ".ruff_cache",
    }
    if context == "backend":
        files = [
            d.ROOT / context / p
            for p in (
                "Dockerfile",
                "pyproject.toml",
                "requirements.lock",
                "requirements-model.lock",
            )
        ]
        files += list((d.ROOT / context / "app").rglob("*.py"))
    else:
        files = []
        for directory, subdirs, names in os.walk(d.ROOT / context):
            subdirs[:] = [p for p in subdirs if p not in excluded]
            files.extend(Path(directory) / n for n in names if not n.startswith(".env"))
    d.require(files, f"Build context contains no fingerprint inputs: {context}.")
    for path in sorted(files):
        d.require(
            path.is_file(),
            f"Build fingerprint input is missing or not a file: {path.relative_to(d.ROOT)}.",
        )
        digest.update(str(path.relative_to(d.ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def reusable_images(owner, fingerprints, arch):
    d = api()
    reusable = {}
    for name, fingerprint in fingerprints.items():
        image = (owner or {}).get("images", {}).get(name)
        if image and owner.get("build_fingerprints", {}).get(name) == fingerprint:
            result = d.run(
                ["docker", "image", "inspect", image, "--format", "{{.Architecture}} {{.Id}}"],
                check=False,
            )
            if result.returncode == 0 and result.stdout.split()[0] == arch:
                expected = owner.get("image_ids", {}).get(name)
                if expected and result.stdout.split()[1] == expected:
                    reusable[name] = image
    return reusable
