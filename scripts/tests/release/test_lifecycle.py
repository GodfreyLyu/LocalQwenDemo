"""Real Git release histories with mocked GitHub PR/status transport; no live mutations."""

import copy
import json

import pytest
import release.lifecycle as lifecycle
import release.publisher as publish
import release.snapshot_impl as snapshot
import validation.helm as helm
import yaml

from scripts.tests.support.paths import ROOT
from scripts.tests.support.release import committed, first_candidate, git, make_candidate, next_run

pytestmark = [pytest.mark.integration, pytest.mark.requires_git, pytest.mark.requires_helm]


@pytest.fixture
def lifecycle_api(publishing, monkeypatch):
    state, _ = publishing
    original = publish.api

    def api(repository, path, payload=None):
        if path.startswith("pulls/"):
            pr = state["prs"][int(path.split("/")[1]) - 1]
            return {
                "merged": pr["state"] == "MERGED",
                "state": "closed" if pr["state"] != "OPEN" else "open",
                "head": {
                    "ref": pr["headRefName"],
                    "sha": pr["headRefOid"],
                    "repo": {"full_name": "Owner/Repo"},
                },
                "base": {"ref": publish.BASE, "repo": {"full_name": "Owner/Repo"}},
                "merge_commit_sha": pr.get("merged_sha"),
            }
        return original(repository, path, payload)

    monkeypatch.setattr(publish, "api", api)
    return state


def approve(source, pr, state, method="squash"):
    head = pr["headRefOid"]
    base = publish.manifest_at(source, head)["candidate"]["baseSha"]
    extra = ["-p", head] if method == "merge" else []
    merged = git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit-tree",
        head + "^{tree}",
        "-p",
        base,
        *extra,
        "-m",
        f"Approve PR {pr['number']}",
    )
    git(source, "push", "-q", "origin", merged + ":refs/heads/" + publish.BASE)
    pr["state"] = "MERGED"
    pr["merged_sha"] = merged
    state["success"].add(head)
    return merged


def approved_pair(source, tmp_path, publishing, lifecycle_api, monkeypatch, tag_first=True):
    old = first_candidate(source, tmp_path, publishing)
    approve(source, old, lifecycle_api)
    if tag_first:
        lifecycle.finalize(source, "Owner/Repo", old["number"])
    committed(source, "frontend/main.js", "broken frontend v2")
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    next_run(monkeypatch, 2)
    make_candidate(source, tmp_path / "second")
    publish.publish(source, tmp_path / "second", "Owner/Repo")
    new = lifecycle_api["prs"][1]
    approve(source, new, lifecycle_api)
    lifecycle.finalize(source, "Owner/Repo", new["number"])
    old_manifest = publish.manifest_at(source, old["merged_sha"])
    return old, new, "deploy/v" + old_manifest["chartVersion"]


@pytest.mark.parametrize("method", ["squash", "merge"])
def test_finalize_tags_exact_merged_and_source_commits_and_is_idempotent(
    source, tmp_path, publishing, lifecycle_api, method
):
    pr = first_candidate(source, tmp_path, publishing)
    merged = approve(source, pr, lifecycle_api, method)
    manifest = publish.manifest_at(source, merged)
    lifecycle.finalize(source, "Owner/Repo", pr["number"])
    tags_before = git(source, "ls-remote", "--tags", "origin")
    lifecycle.finalize(source, "Owner/Repo", pr["number"])
    assert git(source, "ls-remote", "--tags", "origin") == tags_before
    deploy, record = lifecycle.remote_tag(source, "deploy/v" + manifest["chartVersion"])
    assert deploy == merged and deploy != pr["headRefOid"]
    assert record["candidateSha"] == pr["headRefOid"]
    assert lifecycle.remote_tag(source, "v" + manifest["chartVersion"]) == (
        manifest["sourceSha"],
        record,
    )


@pytest.mark.parametrize("state", ["OPEN", "CLOSED"])
def test_finalize_refuses_unmerged_pr(source, tmp_path, publishing, lifecycle_api, state):
    pr = first_candidate(source, tmp_path, publishing)
    pr["state"] = state
    with pytest.raises(ValueError, match="not merged"):
        lifecycle.finalize(source, "Owner/Repo", pr["number"])
    assert not git(source, "ls-remote", "--tags", "origin")


def test_finalize_historical_pr_after_newer_release(
    source, tmp_path, publishing, lifecycle_api, monkeypatch
):
    old, new, tag = approved_pair(
        source, tmp_path, publishing, lifecycle_api, monkeypatch, tag_first=False
    )
    assert lifecycle.remote_tag(source, tag) is None
    lifecycle.finalize(source, "Owner/Repo", old["number"])
    assert lifecycle.remote_tag(source, tag)[0] == old["merged_sha"]
    assert publish.remote(source, publish.BASE) == new["merged_sha"]


def test_tag_collision_is_rejected_before_creating_other_tag(
    source, tmp_path, publishing, lifecycle_api
):
    pr = first_candidate(source, tmp_path, publishing)
    approve(source, pr, lifecycle_api)
    manifest = publish.manifest_at(source, pr["headRefOid"])
    tag = "v" + manifest["chartVersion"]
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "tag",
        "-a",
        tag,
        "HEAD",
        "-m",
        "{}",
    )
    git(source, "push", "-q", "origin", "refs/tags/" + tag)
    with pytest.raises(ValueError, match="conflicts"):
        lifecycle.finalize(source, "Owner/Repo", pr["number"])
    assert lifecycle.remote_tag(source, "deploy/" + tag) is None


def test_failed_atomic_push_can_retry_without_moving_tags(
    source, tmp_path, publishing, lifecycle_api, monkeypatch
):
    pr = first_candidate(source, tmp_path, publishing)
    approve(source, pr, lifecycle_api)
    original = publish.command

    def fail(*args, **kwargs):
        if args[:3] == ("git", "push", "--atomic"):
            raise RuntimeError("connection interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(publish, "command", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        lifecycle.finalize(source, "Owner/Repo", pr["number"])
    assert not git(source, "ls-remote", "--tags", "origin")
    monkeypatch.setattr(publish, "command", original)
    lifecycle.finalize(source, "Owner/Repo", pr["number"])
    assert len(git(source, "ls-remote", "--tags", "origin").splitlines()) == 4


@pytest.mark.parametrize("change", ["validation", "merged-tree", "source-history", "fork"])
def test_finalize_rejects_untrusted_provenance(
    source, tmp_path, publishing, lifecycle_api, monkeypatch, change
):
    pr = first_candidate(source, tmp_path, publishing)
    approve(source, pr, lifecycle_api)
    if change == "validation":
        lifecycle_api["success"].clear()
    elif change == "merged-tree":
        checkout = tmp_path / "modified-merge"
        git(source, "worktree", "add", "--detach", str(checkout), pr["merged_sha"])
        committed(checkout, "README.md", "unreviewed change")
        # Same release metadata/base, but an unreviewed merged tree.
        base = publish.manifest_at(source, pr["headRefOid"])["candidate"]["baseSha"]
        pr["merged_sha"] = git(
            checkout,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit-tree",
            "HEAD^{tree}",
            "-p",
            base,
            "-m",
            "Incorrect merge",
        )
        git(source, "push", "-q", "origin", pr["merged_sha"] + ":refs/heads/test-merge")
    elif change == "source-history":
        # A source commit from the release-only history is not part of main.
        manifest = publish.manifest_at(source, pr["merged_sha"])
        manifest["sourceSha"] = pr["merged_sha"]
        with pytest.raises(ValueError, match="history"):
            lifecycle.source_history(source, manifest)
        return
    else:
        original = publish.api

        def fork(repository, path, payload=None):
            data = original(repository, path, payload)
            if path.startswith("pulls/"):
                data["head"]["repo"]["full_name"] = "Other/Repo"
            return data

        monkeypatch.setattr(publish, "api", fork)
    expected = "differs from the reviewed" if change == "merged-tree" else ".*"
    with pytest.raises(ValueError, match=expected):
        lifecycle.finalize(source, "Owner/Repo", pr["number"])
    assert not git(source, "ls-remote", "--tags", "origin")


def test_rollback_restores_exact_snapshot_with_new_identity_and_survives_normal_release(
    source, tmp_path, publishing, lifecycle_api, monkeypatch
):
    old, new, tag = approved_pair(source, tmp_path, publishing, lifecycle_api, monkeypatch)
    target = publish.manifest_at(source, old["merged_sha"])
    next_run(monkeypatch, 3)
    workspace = tmp_path / "rollback"
    lifecycle.prepare_rollback(source, workspace, "Owner/Repo", tag, "Frontend regression")
    candidate = workspace / "candidate"
    manifest = json.loads((candidate / "release.json").read_text())
    assert manifest["images"] == target["images"]
    assert manifest["inputs"] == target["inputs"]
    assert manifest["sourceSha"] == target["sourceSha"]
    assert manifest["candidate"]["baseSha"] == new["merged_sha"]
    assert manifest["chartVersion"].endswith("-rc.3")
    assert manifest["rollback"]["targetSha"] == old["merged_sha"]
    helm.validate_tree(candidate)
    resources = helm.render(
        candidate, [candidate / "release-values.yaml", candidate / helm.MINIKUBE_VALUES]
    )
    helm.validate_snapshot(candidate, helm.validate_resources(resources))
    publish.publish(source, workspace, "Owner/Repo")
    rollback_pr = lifecycle_api["prs"][-1]
    publish.verify_snapshot(source, rollback_pr["headRefOid"])
    lifecycle_api["success"].add(rollback_pr["headRefOid"])
    # Same run: exact candidate and PR are reused after interruption.
    retry = tmp_path / "retry"
    lifecycle.prepare_rollback(source, retry, "Owner/Repo", tag, "Frontend regression")
    publish.publish(source, retry, "Owner/Repo")
    assert len(lifecycle_api["prs"]) == 3
    with pytest.raises(ValueError, match="differs"):
        lifecycle.prepare_rollback(source, tmp_path / "changed-retry", "Owner/Repo", tag, "Other")
    # An unchanged main must compare against B, not select the pending rollback to A.
    next_run(monkeypatch, 4)
    publish.prepare(source, tmp_path / "unchanged", "Owner/Repo")
    assert json.loads((tmp_path / "unchanged/state.json").read_text())["mode"] == "noop"
    # A newer normal candidate must not reuse or automatically close an incident rollback.
    committed(source, "frontend/main.js", "fixed frontend v3")
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    next_run(monkeypatch, 5)
    make_candidate(source, tmp_path / "fix")
    publish.publish(source, tmp_path / "fix", "Owner/Repo")
    fixed = lifecycle_api["prs"][-1]
    lifecycle_api["success"].add(fixed["headRefOid"])
    publish.supersede(source, "Owner/Repo", fixed["headRefOid"])
    assert rollback_pr["state"] == "OPEN"
    approve(source, rollback_pr, lifecycle_api)
    lifecycle.finalize(source, "Owner/Repo", rollback_pr["number"])
    assert lifecycle.remote_tag(source, "v" + manifest["chartVersion"])[0] == target["sourceSha"]
    with pytest.raises(ValueError, match="release base changed"):
        publish.verify_snapshot(source, fixed["headRefOid"])
    # A subsequent main revert matching the restored inputs needs no empty deployment.
    committed(source, "frontend/main.js", "console.log('frontend')\n")
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    next_run(monkeypatch, 6)
    publish.prepare(source, tmp_path / "reverted", "Owner/Repo")
    assert json.loads((tmp_path / "reverted/state.json").read_text())["mode"] == "noop"


@pytest.mark.parametrize("change", ["values", "image-source", "chart", "target", "reason"])
def test_rollback_admission_rejects_drift(
    source, tmp_path, publishing, lifecycle_api, monkeypatch, change
):
    _, _, tag = approved_pair(source, tmp_path, publishing, lifecycle_api, monkeypatch)
    next_run(monkeypatch, 3)
    workspace = tmp_path / "rollback"
    lifecycle.prepare_rollback(source, workspace, "Owner/Repo", tag, "Regression")
    candidate = workspace / "candidate"
    manifest_path = candidate / "release.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "values":
        (candidate / "release-values.yaml").write_text("changed: true\n")
    elif change == "chart":
        chart_path = candidate / snapshot.CHART / "Chart.yaml"
        chart = yaml.safe_load(chart_path.read_text())
        chart["description"] = "unrelated change"
        chart_path.write_text(yaml.safe_dump(chart))
    elif change == "image-source":
        manifest["images"]["backend"]["sourceSha"] = "e" * 40
    elif change == "target":
        manifest["rollback"]["targetSha"] = "f" * 40
    else:
        manifest["rollback"]["reason"] = " "
    manifest_path.write_text(json.dumps(manifest))
    committed(candidate, "release.json", manifest_path.read_text())
    sha = git(candidate, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="Rollback"):
        lifecycle.verify_rollback(source, sha, manifest)


def test_rollback_rejects_current_release_and_malformed_or_missing_tag(
    source, tmp_path, publishing, lifecycle_api, monkeypatch
):
    _, new, _ = approved_pair(source, tmp_path, publishing, lifecycle_api, monkeypatch)
    current_tag = "deploy/v" + publish.manifest_at(source, new["merged_sha"])["chartVersion"]
    for tag in (current_tag, "main", "deploy/v9.9.9-rc.999"):
        with pytest.raises(ValueError):
            lifecycle.prepare_rollback(source, tmp_path / "rejected", "Owner/Repo", tag, "Problem")
    assert not (tmp_path / "rejected").exists()


def test_workflow_wiring_uses_one_version_sequence_and_trusted_finalizer():
    release = yaml.load(
        (ROOT / ".github/workflows/release-candidate.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert "rollback_tag" in release["on"]["workflow_dispatch"]["inputs"]
    assert release["jobs"]["rollback"]["needs"] == "quality"
    assert (
        release["jobs"]["validate-rollback"]["uses"]
        == "./.github/workflows/deployment-validation.yml"
    )
    assert release["jobs"]["supersede"]["needs"] == ["candidate", "validate-candidate"]
    finalize = yaml.load(
        (ROOT / ".github/workflows/release-finalize.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert finalize["on"]["pull_request_target"]["types"] == ["closed"]
    assert "merged == true" in finalize["jobs"]["tag"]["if"]
    assert finalize["jobs"]["tag"]["steps"][0]["with"]["ref"] == "main"
    assert finalize["jobs"]["tag"]["permissions"]["contents"] == "write"


def test_rollback_metadata_cannot_be_hidden_on_normal_release(source, tmp_path):
    from release.metadata import validate_candidate

    from scripts.tests.support.release import released

    manifest = copy.deepcopy(released(source, tmp_path))
    manifest["rollback"] = {"reason": "not explicitly classified"}
    with pytest.raises(ValueError, match="requires rollback"):
        validate_candidate(manifest)
