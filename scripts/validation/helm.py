"""Offline checks for source Charts and complete deployment-release snapshots."""

import argparse
import subprocess
from pathlib import Path

import yaml
from tooling_paths import ROOT

from validation.constants import CHART, MINIKUBE_VALUES
from validation.resources import require, validate_resources
from validation.snapshot import validate_snapshot, validate_tree


def render(root, values=(), settings=()):
    command = [
        "helm",
        "template",
        "local-review",
        str(root / CHART),
        "--namespace",
        "review-validation",
    ]
    for path in values:
        command += ["-f", str(path)]
    for setting in settings:
        command += ["--set", setting]
    return list(yaml.safe_load_all(subprocess.check_output(command, text=True)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.snapshot:
        validate_tree(root)
    values = [root / "release-values.yaml"] if args.snapshot else []
    values.append(root / MINIKUBE_VALUES)
    subprocess.run(
        ["helm", "lint", str(root / CHART), *sum((["-f", str(v)] for v in values), [])],
        check=True,
    )
    keyed = validate_resources(render(root, values))
    require(
        not any(kind == "NetworkPolicy" for kind, _ in keyed),
        "The Minikube profile must explicitly disable network policies",
    )
    if args.snapshot:
        validate_snapshot(root, keyed)
    validate_resources(
        render(
            root,
            values,
            settings=[
                "networkPolicy.enabled=true",
                "volumePermissions.enabled=false",
            ],
        )
    )
    validate_resources(
        render(
            root,
            values,
            settings=[
                "networkPolicy.enabled=true",
                "persistence.history.existingClaim=retained-history",
            ],
        )
    )
    print("Helm configuration and deployment invariants validated.")


def entry():
    main()


if __name__ == "__main__":
    entry()
