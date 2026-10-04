"""Create deployment-release once, without importing application source or history."""

import argparse
import json
import subprocess
from pathlib import Path


def api(path, payload=None):
    command = ["gh", "api", path]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    return json.loads(
        subprocess.check_output(command, input=json.dumps(payload) if payload else None, text=True)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="GodfreyLyu/LocalQwenDemo")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    branches = api(f"repos/{args.repository}/git/matching-refs/heads/deployment-release")
    if any(b["ref"] == "refs/heads/deployment-release" for b in branches):
        print("deployment-release already exists; no changes made.")
        return
    entry = (
        (root / "deploy/release/validation-entry.yml")
        .read_text()
        .replace("GodfreyLyu/LocalQwenDemo", args.repository)
    )
    tree = api(
        f"repos/{args.repository}/git/trees",
        {
            "tree": [
                {
                    "path": "README.md",
                    "mode": "100644",
                    "type": "blob",
                    "content": (
                        "# Deployment releases\n\n"
                        "This branch awaits its first generated release PR from main.\n"
                        "Only Helm deployment artifacts belong here. "
                        "Review deployment/snapshot before merging.\n"
                    ),
                },
                {
                    "path": ".github/workflows/deployment-validation.yml",
                    "mode": "100644",
                    "type": "blob",
                    "content": entry,
                },
            ]
        },
    )
    commit = api(
        f"repos/{args.repository}/git/commits",
        {
            "message": "Initialize deployment release branch",
            "tree": tree["sha"],
            "parents": [],
        },
    )
    api(
        f"repos/{args.repository}/git/refs",
        {"ref": "refs/heads/deployment-release", "sha": commit["sha"]},
    )
    print(f"Created deployment-release at {commit['sha']}; application source was not copied.")


if __name__ == "__main__":
    main()
