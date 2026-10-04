"""GitHub release PR orchestration. Run only from trusted main after quality checks."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from snapshot import plan

BASE = "deployment-release"
HEAD = "automation/deployment-release"


def command(*args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(
            f"{args[0]} {args[1]} failed (exit {result.returncode}); check repository permissions"
        )
    return result.stdout.strip()


def remote(root, branch):
    rows = command("git", "ls-remote", "--heads", "origin", "refs/heads/" + branch, cwd=root)
    return rows.split()[0] if rows else ""


def output(name, value):
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"{name}={value}\n")


def prepare(root, workspace, repository, force=False):
    base_sha = remote(root, BASE)
    if not base_sha:
        raise ValueError("Bootstrap deployment-release using scripts/release/bootstrap.py first")
    command("git", "fetch", "origin", "refs/heads/" + BASE, cwd=root)
    head_sha = remote(root, HEAD)
    prs = json.loads(
        command(
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--base",
            BASE,
            "--head",
            HEAD,
            "--state",
            "open",
            "--json",
            "number,headRefOid",
        )
    )
    # Closed/rejected candidates must not become the approved baseline.
    baseline = base_sha
    if prs and head_sha and prs[0]["headRefOid"] == head_sha:
        command("git", "fetch", "origin", "refs/heads/" + HEAD, cwd=root)
        baseline = head_sha
    previous_text = command("git", "show", baseline + ":release.json", cwd=root, check=False)
    previous = json.loads(previous_text) if previous_text else {}
    candidate = plan(root, previous, repository)
    if force:
        candidate["changed"] = True
        for component in candidate["images"]:
            candidate["images"][component].pop("digest", None)
            candidate["images"][component]["sourceSha"] = candidate["sourceSha"]
            candidate["build"][component] = True
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "plan.json").write_text(json.dumps(candidate, indent=2) + "\n")
    (workspace / "state.json").write_text(json.dumps({"base": base_sha, "head": head_sha}))
    command(
        "git",
        "worktree",
        "add",
        "--detach",
        str(workspace / "candidate"),
        base_sha,
        cwd=root,
    )
    output("changed", str(bool(candidate["changed"])).lower())
    output("existing_candidate_sha", head_sha if prs else "")
    for component in ("backend", "frontend"):
        output(component + "_build", str(candidate["build"][component]).lower())
        output(component + "_repository", candidate["images"][component]["repository"])
        output(component + "_digest", candidate["images"][component].get("digest", ""))


def publish(root, workspace, repository):
    candidate = workspace / "candidate"
    manifest = json.loads((candidate / "release.json").read_text())
    state = json.loads((workspace / "state.json").read_text())
    if remote(root, "main") != manifest["sourceSha"]:
        output("published", "false")
        print("A newer main commit exists; this candidate will not replace it.")
        return
    if remote(root, BASE) != state["base"]:
        raise ValueError("Release base changed during this run; rerun the latest main workflow")
    command("git", "add", "--all", cwd=candidate)
    changed = command("git", "diff", "--cached", "--name-only", cwd=candidate)
    if not changed:
        # Still create a missing PR after a previous run was interrupted after pushing.
        print("Candidate matches approved deployment; no PR is needed.")
        output("published", "false")
        return
    command(
        "git",
        "-c",
        "user.name=github-actions[bot]",
        "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit",
        "-m",
        "Release " + manifest["sourceSha"][:12],
        cwd=candidate,
    )
    sha = command("git", "rev-parse", "HEAD", cwd=candidate)
    command(
        "git",
        "push",
        "--force-with-lease=refs/heads/" + HEAD + ":" + state["head"],
        "origin",
        "HEAD:refs/heads/" + HEAD,
        cwd=candidate,
    )
    previous_text = command("git", "show", state["base"] + ":release.json", cwd=root, check=False)
    previous = json.loads(previous_text) if previous_text else {}
    source = manifest["sourceSha"]
    body = [
        f"Deployment snapshot from [{source[:12]}](https://github.com/{repository}/commit/{source}).",
        "",
        f"Chart version: `{manifest['chartVersion']}`.",
        "",
        "| Component | Previous digest | Candidate digest | Source |",
        "| --- | --- | --- | --- |",
    ]
    for component, entry in manifest["images"].items():
        old = previous.get("images", {}).get(component, {}).get("digest", "First release")
        body.append(
            f"| {component} | `{old}` | `{entry['digest']}` | `{entry['sourceSha'][:12]}` |"
        )
    body += [
        "",
        "Changed deployment files:",
        "",
        *[f"- `{p}`" for p in changed.splitlines()],
        "",
        "Source quality checks, image publication and offline Helm validation passed "
        "before this PR was updated.",
        "The `deployment/snapshot` commit status records the separate candidate validation result.",
        "Merging approves the deployment snapshot; "
        "cluster deployment remains an explicit operation.",
        "",
        f"[Workflow run](https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']})",
    ]
    body_file = workspace / "pr-body.md"
    body_file.write_text("\n".join(body) + "\n")
    prs = json.loads(
        command(
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--base",
            BASE,
            "--head",
            HEAD,
            "--state",
            "open",
            "--json",
            "number",
        )
    )
    title = "Deploy main " + source[:12]
    if prs:
        command(
            "gh",
            "pr",
            "edit",
            str(prs[0]["number"]),
            "--repo",
            repository,
            "--title",
            title,
            "--body-file",
            str(body_file),
        )
    else:
        command(
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            BASE,
            "--head",
            HEAD,
            "--title",
            title,
            "--body-file",
            str(body_file),
        )
    output("published", "true")
    output("candidate_sha", sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "publish"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.root.resolve(), args.workspace.resolve(), args.repository, args.force)
    else:
        publish(args.root.resolve(), args.workspace.resolve(), args.repository)


if __name__ == "__main__":
    main()
