"""Tag approved snapshots and prepare exact-content rollback PRs; never deploy or merge."""

import copy
import json
import os
import re

import yaml

from release import publisher as pub
from release.metadata import SHA, VERSION, require, validate_candidate
from release.snapshot_impl import CHART, COMPONENTS, FORMAT, candidate_metadata


def ancestor(root, older, newer):
    require(
        re.fullmatch(SHA, older or "") and re.fullmatch(SHA, newer or ""), "Invalid ancestry commit"
    )
    # Missing commits and unrelated histories both fail closed (empty merge-base output).
    require(
        pub.command("git", "merge-base", older, newer, cwd=root, check=False) == older,
        "Commit is not in the expected branch history",
    )


def source_history(root, manifest):
    require(manifest.get("schemaVersion") == FORMAT, "Only format-3 releases are supported")
    validate_candidate(manifest)
    main = pub.remote(root, "main")
    pub.fetch_branch(root, "main")
    for sha in {manifest["sourceSha"], *(v["sourceSha"] for v in manifest["images"].values())}:
        ancestor(root, sha, main)


def remote_tag(root, name):
    """Read a remote annotated tag without trusting or modifying a local tag of that name."""
    rows = pub.command("git", "ls-remote", "--tags", "origin", "refs/tags/" + name, cwd=root)
    if not rows:
        return None
    oid = rows.split()[0]
    pub.command("git", "fetch", "--no-tags", "origin", "refs/tags/" + name, cwd=root)
    require(
        pub.command("git", "cat-file", "-t", oid, cwd=root) == "tag",
        "Release tags must be annotated",
    )
    text = pub.command("git", "cat-file", "tag", oid, cwd=root)
    headers, message = text.split("\n\n", 1)
    fields = dict(line.split(" ", 1) for line in headers.splitlines())
    require(
        fields.get("type") == "commit" and fields.get("tag") == name,
        "Unexpected release tag object",
    )
    return fields["object"], json.loads(message)


def finalize(root, repository, pr_number):
    """Use the merged PR's exact commit, including on retries after newer releases merge."""
    require(type(pr_number) is int and pr_number > 0, "A positive PR number is required")
    pr = pub.api(repository, f"pulls/{pr_number}")
    require(pr.get("merged") is True and pr.get("state") == "closed", "PR is not merged")
    require(pr["base"]["ref"] == pub.BASE, "PR does not target deployment-release")
    require(
        all(
            pr[side]["repo"]["full_name"].lower() == repository.lower() for side in ("base", "head")
        ),
        "PR must belong to this repository",
    )
    head, merged = pr["head"]["sha"], pr["merge_commit_sha"]
    require(
        re.fullmatch(SHA, head or "") and re.fullmatch(SHA, merged or ""),
        "Invalid merged PR commit",
    )
    for sha in (head, merged):
        pub.command("git", "fetch", "origin", sha, cwd=root)
    manifest = pub.manifest_at(root, merged)
    source_history(root, manifest)
    require(manifest["repository"].lower() == repository.lower(), "Wrong snapshot repository")
    candidate = manifest["candidate"]
    require(pr["head"]["ref"] == candidate["branch"], "PR candidate branch mismatch")
    require(pub.remote(root, candidate["branch"]) == head, "Candidate reference changed")
    require(pub.parents(root, head) == [candidate["baseSha"]], "Candidate base mismatch")
    require(pub.parents(root, merged)[0] == candidate["baseSha"], "Merged release base mismatch")
    require(
        pub.command("git", "rev-parse", head + "^{tree}", cwd=root)
        == pub.command("git", "rev-parse", merged + "^{tree}", cwd=root),
        "Merged snapshot differs from the reviewed candidate",
    )
    base = pub.remote(root, pub.BASE)
    pub.fetch_branch(root, pub.BASE)
    ancestor(root, merged, base)
    require(
        pub.validation_passed(repository, head), "Candidate lacks successful snapshot validation"
    )
    verify_rollback(root, head, manifest)
    version = candidate["version"]
    record = {
        "version": version,
        "sourceSha": manifest["sourceSha"],
        "deploymentSha": merged,
        "candidateSha": head,
        "pullRequest": pr_number,
        "runId": candidate["runId"],
        "operation": manifest.get("operation", "release"),
    }
    if "rollback" in manifest:
        record["rollback"] = manifest["rollback"]
    tags = {"deploy/v" + version: merged, "v" + version: manifest["sourceSha"]}
    missing = []
    # Preflight both names before any ref mutation; atomic push prevents partial pairs.
    for name, sha in tags.items():
        existing = remote_tag(root, name)
        if existing is None:
            missing.append(name)
        else:
            require(existing == (sha, record), "Existing tag conflicts with release identity")
    for name in missing:
        # A fresh checkout normally has no local tag. Never overwrite one even locally.
        local = pub.command("git", "tag", "--list", name, cwd=root)
        if local:
            require(
                pub.command("git", "cat-file", "-t", "refs/tags/" + name, cwd=root) == "tag",
                "Local release tag must be annotated",
            )
            require(
                pub.command("git", "rev-parse", "refs/tags/" + name + "^{}", cwd=root)
                == tags[name],
                "Local tag conflicts with release identity",
            )
            message = pub.command("git", "cat-file", "tag", "refs/tags/" + name, cwd=root)
            require(
                json.loads(message.split("\n\n", 1)[1]) == record,
                "Local tag conflicts with release identity",
            )
        else:
            pub.command(
                "git",
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "tag",
                "-a",
                name,
                tags[name],
                "-m",
                json.dumps(record, sort_keys=True),
                cwd=root,
            )
    if missing:
        pub.command(
            "git",
            "push",
            "--atomic",
            "origin",
            *(f"refs/tags/{name}:refs/tags/{name}" for name in missing),
            cwd=root,
        )
    print(f"Recorded {version}: deployment {merged}, source {manifest['sourceSha']}")


def rollback_target(root, repository, tag, base):
    require(re.fullmatch("deploy/v" + VERSION, tag or ""), "Select an approved deploy/v tag")
    target = remote_tag(root, tag)
    require(target is not None, "Deployment tag does not exist; finalize its merged PR first")
    sha, record = target
    ancestor(root, sha, base)
    require(sha != base, "Rollback target is already the current release")
    manifest = pub.manifest_at(root, sha)
    source_history(root, manifest)
    require(manifest["repository"].lower() == repository.lower(), "Wrong snapshot repository")
    require(
        tag == "deploy/v" + manifest["candidate"]["version"]
        and record.get("deploymentSha") == sha
        and record.get("sourceSha") == manifest["sourceSha"],
        "Deployment tag provenance mismatch",
    )
    require(
        remote_tag(root, tag.removeprefix("deploy/")) == (manifest["sourceSha"], record),
        "Source/deployment tag pair mismatch",
    )
    return sha, manifest


def rollback_manifest(target, metadata, target_tag, target_sha, reason):
    manifest = copy.deepcopy(target)
    manifest["candidate"] = metadata
    manifest["chartVersion"] = metadata["version"]
    manifest["chartVersions"]["local-review"] = metadata["version"]
    manifest["operation"] = "rollback"
    manifest["rollback"] = {
        "of": metadata["baseSha"],
        "targetTag": target_tag,
        "targetSha": target_sha,
        "reason": reason.strip(),
    }
    validate_candidate(manifest)
    return manifest


def prepare_rollback(root, workspace, repository, target_tag, reason):
    require(
        isinstance(reason, str) and 0 < len(reason.strip()) <= 1000,
        "Rollback reason must contain 1 to 1000 characters",
    )
    base = pub.remote(root, pub.BASE)
    pub.fetch_branch(root, pub.BASE)
    target_sha, target = rollback_target(root, repository, target_tag, base)
    metadata = candidate_metadata(
        root, base, os.environ["GITHUB_RUN_NUMBER"], os.environ["GITHUB_RUN_ID"]
    )
    manifest = rollback_manifest(target, metadata, target_tag, target_sha, reason)
    branch = metadata["branch"]
    existing = pub.remote(root, branch)
    if existing:
        pub.fetch_branch(root, branch)
        require(
            pub.manifest_at(root, existing) == manifest and pub.parents(root, existing) == [base],
            "Existing rollback differs; start a new run",
        )
    workspace.mkdir(parents=True, exist_ok=True)
    destination = workspace / "candidate"
    pub.command("git", "worktree", "add", "--detach", str(destination), base, cwd=root)
    # Only this dedicated checkout is reset. HEAD remains the current release base.
    pub.command("git", "read-tree", "--reset", "-u", target_sha, cwd=destination)
    from validation.snapshot import validate_tree

    validate_tree(destination)
    chart_path = destination / CHART / "Chart.yaml"
    chart = yaml.safe_load(chart_path.read_text())
    chart["version"] = metadata["version"]
    chart_path.write_text(yaml.safe_dump(chart, sort_keys=False))
    (destination / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (workspace / "state.json").write_text(
        json.dumps(
            {
                "base": base,
                "head": existing,
                "branch": branch,
                "source": pub.command("git", "rev-parse", "HEAD", cwd=root),
                "mode": "retry" if existing else "new",
            }
        )
    )
    print(f"Prepared rollback {metadata['version']} restoring {target_tag}; review before merging.")


def verify_rollback(root, sha, manifest):
    """Check all restored blobs and metadata, not just image digests or a rollback label."""
    validate_candidate(manifest)
    if manifest.get("operation") != "rollback":
        return
    rollback = manifest["rollback"]
    target_sha, target = rollback_target(
        root, manifest["repository"], rollback["targetTag"], manifest["candidate"]["baseSha"]
    )
    require(rollback["targetSha"] == target_sha, "Rollback target reference changed")
    expected = rollback_manifest(
        target, manifest["candidate"], rollback["targetTag"], target_sha, rollback["reason"]
    )
    require(manifest == expected, "Rollback provenance differs from target")

    def tree(commit):
        return dict(
            line.split("\t", 1)[::-1]
            for line in pub.command("git", "ls-tree", "-r", commit, cwd=root).splitlines()
        )

    original, restored = tree(target_sha), tree(sha)
    chart_path = CHART + "/Chart.yaml"
    for path in ("release.json", chart_path):
        require(
            original.pop(path).startswith("100644 blob ")
            and restored.pop(path).startswith("100644 blob "),
            "Invalid rollback file mode",
        )
    require(original == restored, "Rollback changes files outside release version metadata")
    chart = yaml.safe_load(pub.command("git", "show", target_sha + ":" + chart_path, cwd=root))
    chart["version"] = manifest["candidate"]["version"]
    actual = yaml.safe_load(pub.command("git", "show", sha + ":" + chart_path, cwd=root))
    require(actual == chart, "Rollback Chart differs from target")


def check_images(workspace):
    manifest = json.loads((workspace / "candidate/release.json").read_text())
    for component in COMPONENTS:
        image = manifest["images"][component]
        pub.command(
            "docker", "buildx", "imagetools", "inspect", image["repository"] + "@" + image["digest"]
        )
