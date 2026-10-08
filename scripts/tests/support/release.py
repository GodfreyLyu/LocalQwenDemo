"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import json
import subprocess

import release.publisher as publish  # noqa: E402
import release.snapshot_impl as snapshot  # noqa: E402


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def committed(root, name, content):
    (root / name).write_text(content)
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "change",
    )


def versioned_plan(root, previous=None, number=1):
    result = snapshot.plan(root, previous or {}, "Owner/Repo")
    result["schemaVersion"] = snapshot.FORMAT
    result["candidate"] = snapshot.candidate_metadata(root, "a" * 40, str(number), "123")
    return result


def released(root, tmp_path):
    plan = versioned_plan(root)
    return snapshot.materialize(
        root,
        tmp_path / "release",
        plan,
        {
            "backend": "sha256:" + "a" * 64,
            "frontend": "sha256:" + "b" * 64,
            "ollama": "sha256:" + "d" * 64,
        },
    )


def make_candidate(source, workspace, force=False):
    publish.prepare(source, workspace, "Owner/Repo", force)
    plan = json.loads((workspace / "plan.json").read_text())
    mode = json.loads((workspace / "state.json").read_text())["mode"]
    if mode in {"new", "retry"}:
        snapshot.materialize(
            source,
            workspace / "candidate",
            plan,
            {
                "backend": "sha256:" + "a" * 64,
                "frontend": "sha256:" + "b" * 64,
                "ollama": "sha256:" + "d" * 64,
            },
        )
    return plan


def next_run(monkeypatch, number=2):
    monkeypatch.setenv("GITHUB_RUN_NUMBER", str(number))
    monkeypatch.setenv("GITHUB_RUN_ID", str(122 + number))


def first_candidate(source, tmp_path, publishing):
    workspace = tmp_path / "first"
    make_candidate(source, workspace)
    publish.publish(source, workspace, "Owner/Repo")
    return publishing[0]["prs"][0]
