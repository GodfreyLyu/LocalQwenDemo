"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import json

import pytest
import release.publisher as publish  # noqa: E402
import release.snapshot_impl as snapshot  # noqa: E402

from scripts.tests.support.release import committed, first_candidate, git, make_candidate, next_run


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_publishing_creates_distinct_immutable_versions_and_supersedes_after_validation(
    source, tmp_path, publishing, monkeypatch
):
    state, _ = publishing
    old = first_candidate(source, tmp_path, publishing)
    state["success"].add(old["headRefOid"])
    committed(source, "frontend/main.js", "frontend v2")
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    next_run(monkeypatch)
    workspace = tmp_path / "second"
    plan = make_candidate(source, workspace)
    assert plan["build"] == {"backend": False, "frontend": True, "ollama": False}
    publish.publish(source, workspace, "Owner/Repo")
    new = state["prs"][1]
    chart_version = snapshot.yaml.safe_load((source / snapshot.CHART / "Chart.yaml").read_text())[
        "version"
    ]
    assert new["headRefName"] == f"release-candidate/{chart_version}-rc.2"
    assert old["headRefName"] == f"release-candidate/{chart_version}-rc.1"
    assert old["state"] == "OPEN"
    with pytest.raises(ValueError, match="validated"):
        publish.supersede(source, "Owner/Repo", new["headRefOid"])
    assert old["state"] == "OPEN"
    state["success"].add(new["headRefOid"])
    publish.supersede(source, "Owner/Repo", new["headRefOid"])
    assert old["state"] == "CLOSED" and new["state"] == "OPEN"
    assert publish.remote(source, old["headRefName"]) == old["headRefOid"]


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.recovery
def test_retry_reuses_exact_sha_and_pr_even_with_force(source, tmp_path, publishing):
    state, output = publishing
    old = first_candidate(source, tmp_path, publishing)
    workspace = tmp_path / "retry"
    plan = make_candidate(source, workspace, force=True)
    assert not any(plan["build"].values())
    publish.publish(source, workspace, "Owner/Repo")
    assert len(state["prs"]) == state["refs"] == 1
    assert "candidate_sha=" + old["headRefOid"] in output.read_text()


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.recovery
def test_retry_does_not_rewrite_drifted_snapshot(source, tmp_path, publishing):
    first_candidate(source, tmp_path, publishing)
    workspace = tmp_path / "retry"
    make_candidate(source, workspace)
    (workspace / "candidate/README.md").write_text("drift")
    with pytest.raises(ValueError, match="refusing overwrite"):
        publish.publish(source, workspace, "Owner/Repo")
    assert publishing[0]["refs"] == 1


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.recovery
def test_pr_creation_failure_recovers_without_updating_branch(source, tmp_path, publishing):
    state, _ = publishing
    workspace = tmp_path / "first"
    make_candidate(source, workspace)
    state["fail_pr"] = True
    with pytest.raises(RuntimeError, match="PR creation"):
        publish.publish(source, workspace, "Owner/Repo")
    state["fail_pr"] = False
    retry = tmp_path / "retry"
    make_candidate(source, retry)
    publish.publish(source, retry, "Owner/Repo")
    assert len(state["prs"]) == state["refs"] == 1


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_closed_version_is_never_reopened(source, tmp_path, publishing):
    old = first_candidate(source, tmp_path, publishing)
    old["state"] = "CLOSED"
    workspace = tmp_path / "retry"
    make_candidate(source, workspace)
    with pytest.raises(ValueError, match="closed, merged or changed"):
        publish.publish(source, workspace, "Owner/Repo")
    assert old["state"] == "CLOSED"


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_unchanged_validated_candidate_is_reused_without_empty_pr(
    source, tmp_path, publishing, monkeypatch
):
    state, output = publishing
    old = first_candidate(source, tmp_path, publishing)
    state["success"].add(old["headRefOid"])
    next_run(monkeypatch)
    workspace = tmp_path / "noop"
    make_candidate(source, workspace)
    assert json.loads((workspace / "state.json").read_text())["mode"] == "reuse"
    publish.publish(source, workspace, "Owner/Repo")
    assert len(state["prs"]) == 1
    assert "candidate_sha=" + old["headRefOid"] in output.read_text()


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_force_requires_new_run_and_builds_both(source, tmp_path, publishing, monkeypatch):
    state, _ = publishing
    old = first_candidate(source, tmp_path, publishing)
    state["success"].add(old["headRefOid"])
    next_run(monkeypatch)
    plan = make_candidate(source, tmp_path / "rebuild", force=True)
    assert all(plan["build"].values())
    assert all("digest" not in image for image in plan["images"].values())


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_stale_main_and_changed_release_base_are_not_published(source, tmp_path, publishing):
    state, output = publishing
    workspace = tmp_path / "candidate-run"
    make_candidate(source, workspace)
    committed(source, "frontend/main.js", "newer main")
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    publish.publish(source, workspace, "Owner/Repo")
    assert not state["prs"] and "published=false" in output.read_text()
    workspace = tmp_path / "second-run"
    make_candidate(source, workspace)
    git(source, "push", "-q", "--force", "origin", "HEAD:refs/heads/deployment-release")
    with pytest.raises(ValueError, match="Release base changed"):
        publish.publish(source, workspace, "Owner/Repo")


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_ref_creation_rejects_race_without_creating_pr(source, tmp_path, publishing):
    state, _ = publishing
    workspace = tmp_path / "run"
    make_candidate(source, workspace)
    state["ref_race"] = True
    with pytest.raises(RuntimeError, match="Reference already exists"):
        publish.publish(source, workspace, "Owner/Repo")
    assert not state["prs"]


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_candidate_reference_change_is_rejected(source, tmp_path, publishing):
    workspace = tmp_path / "run"
    plan = make_candidate(source, workspace)
    git(source, "push", "-q", "origin", "HEAD:refs/heads/" + plan["candidate"]["branch"])
    with pytest.raises(ValueError, match="changed concurrently"):
        publish.publish(source, workspace, "Owner/Repo")
    assert not publishing[0]["prs"]


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.recovery
def test_verification_rejects_outdated_base_and_retry_requires_new_run(
    source, tmp_path, publishing
):
    old = first_candidate(source, tmp_path, publishing)
    publish.verify_candidate(source, "Owner/Repo", old["headRefOid"])
    git(source, "push", "-q", "origin", old["headRefOid"] + ":refs/heads/deployment-release")
    with pytest.raises(ValueError, match="release base changed"):
        publish.verify_candidate(source, "Owner/Repo", old["headRefOid"])
    with pytest.raises(ValueError, match="different source, run identity or release base"):
        publish.prepare(source, tmp_path / "retry", "Owner/Repo")


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.recovery
def test_retry_rejects_different_run_id(source, tmp_path, publishing, monkeypatch):
    first_candidate(source, tmp_path, publishing)
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    with pytest.raises(ValueError, match="run identity"):
        publish.prepare(source, tmp_path / "retry", "Owner/Repo")


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_verification_requires_open_same_repository_pr(source, tmp_path, publishing):
    old = first_candidate(source, tmp_path, publishing)
    old["isCrossRepository"] = True
    with pytest.raises(ValueError, match="same-repository"):
        publish.verify_candidate(source, "Owner/Repo", old["headRefOid"])


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_superseding_older_run_does_not_close_newer_candidate(
    source, tmp_path, publishing, monkeypatch
):
    state, _ = publishing
    old = first_candidate(source, tmp_path, publishing)
    state["success"].add(old["headRefOid"])
    next_run(monkeypatch)
    workspace = tmp_path / "second"
    make_candidate(source, workspace, force=True)
    publish.publish(source, workspace, "Owner/Repo")
    publish.supersede(source, "Owner/Repo", old["headRefOid"])
    assert all(p["state"] == "OPEN" for p in state["prs"])


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_legacy_candidate_is_promoted_before_its_pr_is_closed(source, tmp_path, publishing):
    state, _ = publishing
    workspace = tmp_path / "legacy"
    make_candidate(source, workspace)
    checkout = workspace / "candidate"
    manifest_path = checkout / "release.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schemaVersion"] = 1
    del manifest["candidate"]
    manifest_path.write_text(json.dumps(manifest))
    git(checkout, "add", ".")
    git(
        checkout,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "legacy snapshot",
    )
    git(checkout, "push", "-q", "origin", "HEAD:refs/heads/" + publish.LEGACY_HEAD)
    sha = git(checkout, "rev-parse", "HEAD")
    old = {
        "number": 1,
        "headRefName": publish.LEGACY_HEAD,
        "headRefOid": sha,
        "state": "OPEN",
        "isCrossRepository": False,
    }
    state["prs"].append(old)
    state["success"].add(sha)
    workspace = tmp_path / "promote"
    plan = make_candidate(source, workspace)
    assert not any(plan["build"].values())
    publish.publish(source, workspace, "Owner/Repo")
    assert old["state"] == "OPEN"
    new = state["prs"][1]
    state["success"].add(new["headRefOid"])
    publish.supersede(source, "Owner/Repo", new["headRefOid"])
    assert old["state"] == "CLOSED"
    assert publish.remote(source, publish.LEGACY_HEAD) == sha


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_approved_unchanged_release_produces_no_pr(source, tmp_path, publishing, monkeypatch):
    state, output = publishing
    old = first_candidate(source, tmp_path, publishing)
    git(source, "push", "-q", "origin", old["headRefOid"] + ":refs/heads/deployment-release")
    old["state"] = "MERGED"
    next_run(monkeypatch)
    workspace = tmp_path / "noop"
    make_candidate(source, workspace)
    publish.publish(source, workspace, "Owner/Repo")
    assert "published=false" in output.read_text()
    assert len(state["prs"]) == 1


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_snapshot_verification_needs_no_pull_request_api_access(
    source, tmp_path, publishing, monkeypatch
):
    old = first_candidate(source, tmp_path, publishing)

    def denied(*args, **kwargs):
        raise AssertionError("Bootstrap validation must not require PR API permissions")

    monkeypatch.setattr(publish, "pull_requests", denied)
    manifest, base = publish.verify_snapshot(source, old["headRefOid"])
    assert manifest["candidate"]["branch"] == old["headRefName"]
    assert manifest["candidate"]["baseSha"] == base


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_workflow_migration_requires_exact_deletion_and_current_base(source, tmp_path, publishing):
    base = publish.remote(source, publish.BASE)
    checkout = tmp_path / "migration"
    publish.fetch_branch(source, publish.BASE)
    git(source, "worktree", "add", "--detach", str(checkout), base)
    workflow = checkout / ".github/workflows/deployment-validation.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: legacy\n")
    git(checkout, "add", ".")
    git(
        checkout,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "legacy entrypoint",
    )
    git(checkout, "push", "-q", "origin", "HEAD:refs/heads/" + publish.BASE)
    with pytest.raises(ValueError, match="workflow-removal"):
        publish.prepare(source, tmp_path / "blocked", "Owner/Repo")
    workflow.unlink()
    git(checkout, "add", "--all")
    git(
        checkout,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "remove workflow",
    )
    git(checkout, "push", "-q", "origin", "HEAD:refs/heads/" + publish.MIGRATION)
    sha = git(checkout, "rev-parse", "HEAD")
    publish.verify_snapshot(source, sha)
    committed(checkout, "README.md", "unrelated change")
    git(checkout, "push", "-q", "origin", "HEAD:refs/heads/" + publish.MIGRATION)
    with pytest.raises(ValueError, match="Migration may only"):
        publish.verify_snapshot(source, git(checkout, "rev-parse", "HEAD"))
