"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import json
import os
import shutil
import subprocess

import pytest
import release.publisher as publish  # noqa: E402
import release.snapshot_impl as snapshot  # noqa: E402

from scripts.tests.support.paths import ROOT
from scripts.tests.support.release import git


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for directory in (
        "backend/app",
        "frontend",
        "deploy/helm",
        "scripts",
        "docs/guides",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / snapshot.CHART, root / snapshot.CHART)
    shutil.copytree(ROOT / snapshot.OLLAMA_CHART, root / snapshot.OLLAMA_CHART)
    shutil.copytree(ROOT / snapshot.ARGOCD, root / snapshot.ARGOCD)
    shutil.copytree(ROOT / "deploy/images", root / "deploy/images")
    for name in snapshot.DEPLOY_FILES:
        shutil.copyfile(ROOT / name, root / name)
    (root / "backend/app/main.py").write_text("print('backend')\n")
    (root / "frontend/main.js").write_text("console.log('frontend')\n")
    (root / "README.md").write_text("source docs")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "initial",
    )
    return root


@pytest.fixture
def publishing(source, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-q")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q")
    (seed / "README.md").write_text("deployment bootstrap")
    git(seed, "add", ".")
    git(
        seed,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "bootstrap",
    )
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/deployment-release")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "1")
    original = publish.command
    state = {"prs": [], "success": set(), "refs": 0, "fail_pr": False, "ref_race": False}

    def fake_github(*args, **kwargs):
        if args[:3] == ("gh", "pr", "list"):
            selection = args[args.index("--state") + 1]
            return json.dumps(
                [p for p in state["prs"] if selection == "all" or p["state"] == "OPEN"]
            )
        if args[:3] == ("gh", "pr", "create"):
            if state["fail_pr"]:
                raise RuntimeError("PR creation failed")
            branch = args[args.index("--head") + 1]
            state["prs"].append(
                {
                    "number": len(state["prs"]) + 1,
                    "headRefName": branch,
                    "headRefOid": publish.remote(source, branch),
                    "state": "OPEN",
                    "isCrossRepository": False,
                }
            )
            return "https://example.test/pr/1"
        if args[:3] == ("gh", "pr", "close"):
            state["prs"][int(args[3]) - 1]["state"] = "CLOSED"
            return ""
        if args[0] == "gh":
            raise AssertionError(f"Unexpected GitHub operation: {args}")
        return original(*args, **kwargs)

    def fake_api(repository, path, payload=None):
        if path.startswith("commits/"):
            sha = path.split("/")[1]
            return {
                "statuses": [
                    {
                        "context": "deployment/snapshot",
                        "state": "success" if sha in state["success"] else "failure",
                    }
                ]
            }
        if path == "git/trees":
            # Reconstruct the API tree independently, using its actual submitted blobs.
            entries = dict(
                line.split("\t", 1)[::-1]
                for line in git(source, "ls-tree", "-r", payload["base_tree"]).splitlines()
            )
            for entry in payload["tree"]:
                if entry.get("sha", "") is None:
                    entries.pop(entry["path"], None)
                else:
                    sha = original(
                        "git", "hash-object", "-w", "--stdin", cwd=source, data=entry["content"]
                    )
                    entries[entry["path"]] = entry["mode"] + " blob " + sha
            index = tmp_path / "api-index"
            env = dict(os.environ, GIT_INDEX_FILE=str(index))
            subprocess.run(["git", "read-tree", "--empty"], cwd=source, env=env, check=True)
            data = "".join(v.replace(" blob ", " ") + "\t" + k + "\n" for k, v in entries.items())
            subprocess.run(
                ["git", "update-index", "--index-info"],
                cwd=source,
                env=env,
                input=data,
                text=True,
                check=True,
            )
            sha = subprocess.check_output(
                ["git", "write-tree"], cwd=source, env=env, text=True
            ).strip()
            return {"sha": sha}
        if path == "git/commits":
            sha = git(
                source,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit-tree",
                payload["tree"],
                "-p",
                payload["parents"][0],
                "-m",
                payload["message"],
            )
            return {"sha": sha}
        if path == "git/refs":
            if state["ref_race"]:
                raise RuntimeError("Reference already exists")
            branch = payload["ref"].removeprefix("refs/heads/")
            assert not publish.remote(source, branch), "An immutable ref must never be updated"
            git(source, "push", "-q", "origin", payload["sha"] + ":" + payload["ref"])
            state["refs"] += 1
            return {"ref": payload["ref"]}
        raise AssertionError(path)

    monkeypatch.setattr(publish, "command", fake_github)
    monkeypatch.setattr(publish, "api", fake_api)
    return state, output
