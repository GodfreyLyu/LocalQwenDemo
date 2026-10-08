"""Create deployment-release once, without importing application source or history."""

import argparse
import subprocess

from release.github import request as github_request


def api(path, payload=None):
    return github_request(
        path,
        payload,
        execute=lambda args, data: subprocess.check_output(args, input=data, text=True),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="GodfreyLyu/LocalQwenDemo")
    args = parser.parse_args()
    branches = api(f"repos/{args.repository}/git/matching-refs/heads/deployment-release")
    if any(b["ref"] == "refs/heads/deployment-release" for b in branches):
        print("deployment-release already exists; no changes made.")
        return
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


def entry():
    main()


if __name__ == "__main__":
    entry()
