"""Create immutable versioned deployment candidates; retire old PRs only after validation."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from snapshot import candidate_metadata, plan

BASE = "deployment-release"
PREFIX = "release-candidate/"
LEGACY_HEAD = "automation/deployment-release"


def command(*args, cwd=None, check=True, data=None):
    result = subprocess.run(args, cwd=cwd, input=data, text=True, capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(
            f"{args[0]} {args[1]} failed (exit {result.returncode}); check repository permissions"
        )
    return result.stdout.strip()


def api(repository, path, payload=None):
    args = ["gh", "api", f"repos/{repository}/{path}"]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    return json.loads(command(*args, data=json.dumps(payload) if payload is not None else None))


def remote(root, branch):
    rows = command("git", "ls-remote", "--heads", "origin", "refs/heads/" + branch, cwd=root)
    return rows.split()[0] if rows else ""


def output(name, value):
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"{name}={value}\n")


def pull_requests(repository, state="open"):
    return json.loads(
        command(
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--base",
            BASE,
            "--state",
            state,
            "--limit",
            "1000",
            "--json",
            "number,headRefName,headRefOid,state,isCrossRepository",
        )
    )


def managed(pr):
    return not pr["isCrossRepository"] and (
        pr["headRefName"].startswith(PREFIX) or pr["headRefName"] == LEGACY_HEAD
    )


def validation_passed(repository, sha):
    statuses = api(repository, f"commits/{sha}/status")["statuses"]
    return next(
        (s["state"] == "success" for s in statuses if s["context"] == "deployment/snapshot"), False
    )


def manifest_at(root, sha):
    text = command("git", "show", sha + ":release.json", cwd=root, check=False)
    return json.loads(text) if text else {}


def fetch_branch(root, branch):
    command("git", "fetch", "origin", "refs/heads/" + branch, cwd=root)


def parents(root, sha):
    return command("git", "show", "-s", "--format=%P", sha, cwd=root).split()


def prepare(root, workspace, repository, force=False):
    source = command("git", "rev-parse", "HEAD", cwd=root)
    base = remote(root, BASE)
    if not base:
        raise ValueError("Bootstrap deployment-release first")
    fetch_branch(root, BASE)
    metadata = candidate_metadata(
        root, base, os.environ["GITHUB_RUN_NUMBER"], os.environ["GITHUB_RUN_ID"]
    )
    branch = metadata["branch"]
    existing = remote(root, branch)
    mode = "new"
    previous = manifest_at(root, base)
    selected = None
    prs = pull_requests(repository)
    if existing:
        # A workflow rerun reuses its immutable snapshot, including original digests.
        fetch_branch(root, branch)
        previous = manifest_at(root, existing)
        if (
            previous.get("candidate") != metadata
            or previous.get("sourceSha") != source
            or parents(root, existing) != [base]
        ):
            raise ValueError(
                "Existing version has different source, run identity or release base; "
                "start a new run"
            )
        candidate = plan(root, previous, repository)
        if candidate["changed"]:
            raise ValueError("Existing version content differs from this run; refusing overwrite")
        mode = "retry"
    else:
        # Only a validated, open candidate on the current base can avoid a redundant release.
        for pr in prs:
            if not managed(pr) or not validation_passed(repository, pr["headRefOid"]):
                continue
            fetch_branch(root, pr["headRefName"])
            if remote(root, pr["headRefName"]) != pr["headRefOid"]:
                continue
            if parents(root, pr["headRefOid"]) != [base]:
                continue
            pending = manifest_at(root, pr["headRefOid"])
            number = pending.get("candidate", {}).get("runNumber", 0)
            if not selected or number > selected[0]:
                selected = (number, pr, pending)
        if selected:
            previous = selected[2]
        candidate = plan(root, previous, repository)
        if force:
            candidate["changed"] = True
            for component in candidate["images"]:
                candidate["images"][component].pop("digest", None)
                candidate["images"][component]["sourceSha"] = source
                candidate["build"][component] = True
        if not candidate["changed"]:
            # Legacy candidates are explicitly promoted once into the new versioned format.
            if selected and previous.get("schemaVersion") == 2:
                mode = "reuse"
                branch, existing = selected[1]["headRefName"], selected[1]["headRefOid"]
                metadata = previous["candidate"]
            elif not selected:
                mode = "noop"
    candidate["schemaVersion"] = 2
    candidate["candidate"] = metadata
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "plan.json").write_text(json.dumps(candidate, indent=2) + "\n")
    (workspace / "state.json").write_text(
        json.dumps(
            {
                "base": base,
                "head": existing,
                "branch": branch,
                "source": source,
                "mode": mode,
            }
        )
    )
    if mode in {"new", "retry"}:
        command("git", "worktree", "add", "--detach", str(workspace / "candidate"), base, cwd=root)
    output("materialize", str(mode in {"new", "retry"}).lower())
    output("candidate_sha", existing if mode == "reuse" else "")
    output("version", metadata["version"])
    for component in ("backend", "frontend"):
        output(component + "_build", str(candidate["build"][component] and mode == "new").lower())
        output(component + "_repository", candidate["images"][component]["repository"])
        output(component + "_digest", candidate["images"][component].get("digest", ""))


def ensure_pr(root, workspace, repository, manifest, branch, sha, base):
    existing = [
        p for p in pull_requests(repository, "all") if p["headRefName"] == branch and managed(p)
    ]
    if existing:
        pr = existing[0]
        if pr["state"] != "OPEN" or pr["headRefOid"] != sha:
            raise ValueError(
                "Version PR is closed, merged or changed; start a new run rather than reopen it"
            )
        return pr["number"]
    previous = manifest_at(root, base)
    version, source = manifest["chartVersion"], manifest["sourceSha"]
    body = [
        f"Immutable deployment candidate **{version}** from [{source[:12]}](https://github.com/{repository}/commit/{source}).",
        "",
        f"Release base: `{base}`. Candidate commit: `{sha}`.",
        "",
        "| Component | Previous digest | Candidate digest | Image source |",
        "| --- | --- | --- | --- |",
    ]
    for component, image in manifest["images"].items():
        old = previous.get("images", {}).get(component, {}).get("digest", "First release")
        body.append(
            f"| {component} | `{old}` | `{image['digest']}` | `{image['sourceSha'][:12]}` |"
        )
    body += [
        "",
        "This branch is not updated or force-pushed. Changes require a new candidate.",
        "Review the Chart/configuration diff and require `deployment/snapshot` before merging.",
        "Older PRs remain open until this candidate passes validation. "
        "Merging does not deploy a cluster.",
        "",
        f"[Source workflow](https://github.com/{repository}/actions/runs/{manifest['candidate']['runId']})",
    ]
    body_file = workspace / "pr-body.md"
    body_file.write_text("\n".join(body) + "\n")
    command(
        "gh",
        "pr",
        "create",
        "--repo",
        repository,
        "--base",
        BASE,
        "--head",
        branch,
        "--title",
        "Deploy " + version,
        "--body-file",
        str(body_file),
    )
    return next(
        p["number"]
        for p in pull_requests(repository)
        if p["headRefName"] == branch and p["headRefOid"] == sha
    )


def create_ref(root, candidate, repository, branch, base, version):
    """Git Data API creates refs atomically; an existing version can never be updated."""
    # Staged files are already allowlist-validated. Preserve unchanged workflow entrypoints.
    tree_sha = command("git", "write-tree", cwd=candidate)
    names = command(
        "git", "diff", "--cached", "--name-only", "--no-renames", "-z", cwd=candidate
    ).split("\0")
    entries = []
    for name in filter(None, names):
        path = candidate / name
        if path.exists():
            mode = command("git", "ls-files", "--stage", "--", name, cwd=candidate).split()[0]
            entries.append(
                {"path": name, "mode": mode, "type": "blob", "content": path.read_text()}
            )
        else:
            entries.append({"path": name, "mode": "100644", "type": "blob", "sha": None})
    tree = api(
        repository,
        "git/trees",
        {
            "base_tree": command("git", "rev-parse", base + "^{tree}", cwd=root),
            "tree": entries,
        },
    )
    if tree["sha"] != tree_sha:
        raise ValueError("Published tree differs from the validated snapshot")
    commit = api(
        repository,
        "git/commits",
        {"message": "Release " + version, "tree": tree_sha, "parents": [base]},
    )
    api(repository, "git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
    return commit["sha"]


def publish(root, workspace, repository):
    state = json.loads((workspace / "state.json").read_text())
    if remote(root, "main") != state["source"]:
        output("published", "false")
        print("Newer main exists; this run will not publish or supersede a candidate.")
        return
    if remote(root, BASE) != state["base"]:
        raise ValueError("Release base changed; start a new workflow run")
    if state["mode"] == "noop":
        output("published", "false")
        return
    branch = state["branch"]
    if remote(root, branch) != state["head"]:
        raise ValueError("Candidate reference changed concurrently; refusing overwrite")
    if state["mode"] == "reuse":
        sha = state["head"]
        manifest = manifest_at(root, sha)
    else:
        candidate = workspace / "candidate"
        manifest = json.loads((candidate / "release.json").read_text())
        command("git", "add", "--all", cwd=candidate)
        if state["mode"] == "retry":
            sha = state["head"]
            actual = command("git", "write-tree", cwd=candidate)
            expected = command("git", "rev-parse", sha + "^{tree}", cwd=root)
            if actual != expected:
                raise ValueError("Existing version content differs; refusing overwrite")
        else:
            sha = create_ref(
                root, candidate, repository, branch, state["base"], manifest["chartVersion"]
            )
    pr_number = ensure_pr(root, workspace, repository, manifest, branch, sha, state["base"])
    output("published", "true")
    output("candidate_sha", sha)
    output("pr_number", pr_number)


def verify_candidate(root, repository, sha):
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Invalid candidate SHA")
    # Fetch through a PR's branch, never execute anything from that branch.
    matching = [p for p in pull_requests(repository) if managed(p) and p["headRefOid"] == sha]
    if len(matching) != 1:
        raise ValueError("Expected one open same-repository release PR for this commit")
    pr = matching[0]
    branch = pr["headRefName"]
    fetch_branch(root, branch)
    base = remote(root, BASE)
    fetch_branch(root, BASE)
    if remote(root, branch) != sha or parents(root, sha) != [base]:
        raise ValueError("Candidate or release base changed; generate a new candidate")
    manifest = manifest_at(root, sha)
    if manifest.get("schemaVersion") == 2:
        metadata = manifest["candidate"]
        if metadata["branch"] != branch or metadata["baseSha"] != base:
            raise ValueError("Candidate branch/base provenance mismatch")
    elif branch != LEGACY_HEAD:
        raise ValueError("Versioned branches require versioned provenance")
    return pr, manifest, base


def supersede(root, repository, sha):
    pr, manifest, base = verify_candidate(root, repository, sha)
    if manifest.get("schemaVersion") != 2 or not validation_passed(repository, sha):
        raise ValueError("Only a validated versioned candidate can supersede older PRs")
    if remote(root, "main") != command("git", "rev-parse", "HEAD", cwd=root):
        raise ValueError("Main changed before retirement; rerun the latest workflow")
    if plan(root, manifest, repository)["changed"]:
        raise ValueError("Candidate no longer matches current deployable inputs")
    number = manifest["candidate"]["runNumber"]
    for old in pull_requests(repository):
        if old["number"] == pr["number"] or not managed(old):
            continue
        fetch_branch(root, old["headRefName"])
        old_manifest = manifest_at(root, old["headRefOid"])
        old_number = old_manifest.get("candidate", {}).get("runNumber", 0)
        if old_number >= number:
            continue
        # Recheck identities before each mutation, preserving every candidate branch.
        if (
            remote(root, BASE) != base
            or remote(root, pr["headRefName"]) != sha
            or remote(root, "main") != command("git", "rev-parse", "HEAD", cwd=root)
        ):
            raise ValueError("Candidate or release base changed during retirement")
        if remote(root, old["headRefName"]) != old["headRefOid"]:
            raise ValueError("Older candidate changed during retirement")
        command(
            "gh",
            "pr",
            "close",
            str(old["number"]),
            "--repo",
            repository,
            "--comment",
            f"Superseded by #{pr['number']} ({manifest['chartVersion']}) "
            "after successful deployment/snapshot validation. "
            "The version branch is retained for audit.",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "publish", "verify", "supersede"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--candidate-sha")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        prepare(root, args.workspace.resolve(), args.repository, args.force)
    elif args.action == "publish":
        publish(root, args.workspace.resolve(), args.repository)
    elif args.action == "verify":
        verify_candidate(root, args.repository, args.candidate_sha)
    else:
        supersede(root, args.repository, args.candidate_sha)


if __name__ == "__main__":
    main()
