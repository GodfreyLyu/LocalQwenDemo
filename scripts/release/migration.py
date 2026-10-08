"""Propose removal of the legacy release workflow using a maintainer identity.

Run once after main contains the trusted release PR validation entrypoint.
No images, deployment values, existing workloads or approved branch are changed.
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from release.github import request as github_request
from release.publisher import BASE, MIGRATION


def api(repository, path, payload=None):
    return github_request(
        f"repos/{repository}/{path}",
        payload,
        execute=lambda args, data: subprocess.check_output(args, input=data, text=True),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="GodfreyLyu/LocalQwenDemo")
    args = parser.parse_args()
    repo = args.repository
    base = api(repo, "git/ref/heads/" + BASE)["object"]["sha"]
    old = api(repo, "git/commits/" + base)
    tree = api(repo, "git/trees/" + old["tree"]["sha"] + "?recursive=1")
    path = ".github/workflows/deployment-validation.yml"
    if not any(entry["path"] == path for entry in tree["tree"]):
        print("Release branch already has no legacy workflow; no changes made.")
        return
    refs = api(repo, "git/matching-refs/heads/" + MIGRATION)
    if not any(ref["ref"] == "refs/heads/" + MIGRATION for ref in refs):
        new_tree = api(
            repo,
            "git/trees",
            {
                "base_tree": old["tree"]["sha"],
                "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": None}],
            },
        )
        commit = api(
            repo,
            "git/commits",
            {
                "message": "Remove legacy workflow from deployment snapshots",
                "tree": new_tree["sha"],
                "parents": [base],
            },
        )
        api(repo, "git/refs", {"ref": "refs/heads/" + MIGRATION, "sha": commit["sha"]})
    prs = json.loads(
        subprocess.check_output(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                MIGRATION,
                "--base",
                BASE,
                "--state",
                "all",
                "--json",
                "url,state",
            ],
            text=True,
        )
    )
    if prs:
        print(prs[0]["url"])
        return
    with tempfile.TemporaryDirectory() as directory:
        body = Path(directory) / "body.md"
        body.write_text(
            "Remove only the legacy workflow from the deployment branch. "
            "Charts, image digests, release metadata and runtime resources are unchanged.\n\n"
            "Merge the main GitOps implementation PR first. Its trusted validator checks "
            "that this commit has the current release as its only parent and deletes "
            "exactly the legacy workflow. Require deployment/snapshot and review.\n\n"
            "After this PR merges, run Release candidate on main to generate the "
            "first three-image GitOps snapshot.\n"
        )
        subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--base",
                BASE,
                "--head",
                MIGRATION,
                "--title",
                "Migrate release validation to main",
                "--body-file",
                str(body),
            ],
            check=True,
        )


def entry():
    main()


if __name__ == "__main__":
    entry()
