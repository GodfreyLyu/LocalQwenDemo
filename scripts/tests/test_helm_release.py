"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/release"))

import helm_deploy  # noqa: E402
import helm_target  # noqa: E402
import publish  # noqa: E402
import snapshot  # noqa: E402
import validate_helm  # noqa: E402


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


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
    result["schemaVersion"] = 2
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
        },
    )


def test_first_release_builds_both_and_matches_provenance(source, tmp_path):
    manifest = released(source, tmp_path)
    root = tmp_path / "release"
    validate_helm.validate_tree(root)
    resources = validate_helm.render(root, [root / "release-values.yaml"])
    validate_helm.validate_snapshot(root, validate_helm.validate_resources(resources))
    assert manifest["images"]["backend"]["repository"] == "ghcr.io/owner/repo-backend"
    assert not (root / "backend").exists()


def test_noop_docs_and_config_only_reuse_exact_images(source, tmp_path):
    previous = released(source, tmp_path)
    assert not snapshot.plan(source, previous, "Owner/Repo")["changed"]
    committed(source, "README.md", "documentation update")
    assert not snapshot.plan(source, previous, "Owner/Repo")["changed"]
    committed(
        source,
        snapshot.CHART + "/values.yaml",
        (source / snapshot.CHART / "values.yaml")
        .read_text()
        .replace("queueCapacity: 8", "queueCapacity: 6"),
    )
    plan = snapshot.plan(source, previous, "Owner/Repo")
    assert plan["changed"] and not any(plan["build"].values())
    assert plan["images"] == previous["images"]


def test_failed_or_cancelled_intermediate_commit_cannot_hide_changes(source, tmp_path):
    previous = released(source, tmp_path)
    committed(source, "backend/app/main.py", "backend changed")
    # No release from the intermediate commit, then another component changes.
    committed(source, "frontend/main.js", "frontend changed")
    assert snapshot.plan(source, previous, "Owner/Repo")["build"] == {
        "backend": True,
        "frontend": True,
    }


def test_open_candidate_reuses_unchanged_component_source(source, tmp_path):
    previous = released(source, tmp_path)
    committed(source, "frontend/main.js", "frontend changed")
    plan = snapshot.plan(source, previous, "Owner/Repo")
    assert plan["build"] == {"backend": False, "frontend": True}
    assert plan["images"]["backend"]["sourceSha"] == previous["sourceSha"]
    manifest = snapshot.materialize(
        source,
        tmp_path / "release",
        versioned_plan(source, previous, 2),
        {"frontend": "sha256:" + "c" * 64},
    )
    assert not snapshot.plan(source, manifest, "Owner/Repo")["changed"]


def test_deleted_inputs_and_registry_changes_invalidate_reuse(source, tmp_path):
    previous = released(source, tmp_path)
    (source / "backend/app/main.py").unlink()
    git(source, "add", "--all")
    assert snapshot.plan(source, previous, "Owner/Repo")["build"]["backend"]
    assert all(snapshot.plan(source, previous, "Other/Repo")["build"].values())


def test_invalid_digest_and_symlink_are_rejected(source, tmp_path):
    candidate = snapshot.plan(source, {}, "Owner/Repo")
    with pytest.raises(ValueError, match="digest"):
        snapshot.materialize(
            source,
            tmp_path / "bad",
            candidate,
            {"backend": "latest", "frontend": "latest"},
        )
    (source / "frontend/main.js").unlink()
    (source / "frontend/main.js").symlink_to(source / "README.md")
    with pytest.raises(ValueError, match="Symlink"):
        snapshot.plan(source, {}, "Owner/Repo")


def test_schema_rejects_multiple_workers_and_invalid_configuration():
    for setting in (
        "backend.replicas=2",
        "model.cpuThreads=0",
        "config.queueCapacity=100",
        "model.backend=invalid",
    ):
        result = subprocess.run(
            ["helm", "template", "test", str(ROOT / snapshot.CHART), "--set", setting],
            capture_output=True,
        )
        assert result.returncode != 0


def test_config_rollout_and_volume_retention():
    before = validate_helm.validate_resources(validate_helm.render(ROOT))
    after = validate_helm.validate_resources(
        validate_helm.render(
            ROOT,
            settings=[
                "config.queueCapacity=6",
                "volumePermissions.enabled=false",
                "persistence.history.existingClaim=old-history",
            ],
        )
    )
    old = before["Deployment", "review-backend"]["spec"]["template"]
    new = after["Deployment", "review-backend"]["spec"]["template"]
    assert (
        old["metadata"]["annotations"]["checksum/config"]
        != new["metadata"]["annotations"]["checksum/config"]
    )
    assert [i["name"] for i in new["spec"]["initContainers"]] == ["initialize-users"]
    assert ("PersistentVolumeClaim", "review-history") not in after
    assert all(
        r["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
        for (kind, _), r in before.items()
        if kind == "PersistentVolumeClaim"
    )


def test_ollama_rule_is_an_egress_rule():
    resources = validate_helm.validate_resources(
        validate_helm.render(ROOT, settings=["networkPolicy.ollamaHostCidr=192.168.49.1/32"])
    )
    policy = resources["NetworkPolicy", "review-backend"]["spec"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    assert any(
        rule.get("to") == [{"ipBlock": {"cidr": "192.168.49.1/32"}}]
        and rule["ports"] == [{"protocol": "TCP", "port": 11434}]
        for rule in policy["egress"]
    )


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


def make_candidate(source, workspace, force=False):
    publish.prepare(source, workspace, "Owner/Repo", force)
    plan = json.loads((workspace / "plan.json").read_text())
    mode = json.loads((workspace / "state.json").read_text())["mode"]
    if mode in {"new", "retry"}:
        snapshot.materialize(
            source,
            workspace / "candidate",
            plan,
            {"backend": "sha256:" + "a" * 64, "frontend": "sha256:" + "b" * 64},
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
    assert plan["build"] == {"backend": False, "frontend": True}
    publish.publish(source, workspace, "Owner/Repo")
    new = state["prs"][1]
    assert new["headRefName"] == "release-candidate/0.1.0-rc.2"
    assert old["headRefName"] == "release-candidate/0.1.0-rc.1"
    assert old["state"] == "OPEN"
    with pytest.raises(ValueError, match="validated"):
        publish.supersede(source, "Owner/Repo", new["headRefOid"])
    assert old["state"] == "OPEN"
    state["success"].add(new["headRefOid"])
    publish.supersede(source, "Owner/Repo", new["headRefOid"])
    assert old["state"] == "CLOSED" and new["state"] == "OPEN"
    assert publish.remote(source, old["headRefName"]) == old["headRefOid"]


def test_retry_reuses_exact_sha_and_pr_even_with_force(source, tmp_path, publishing):
    state, output = publishing
    old = first_candidate(source, tmp_path, publishing)
    workspace = tmp_path / "retry"
    plan = make_candidate(source, workspace, force=True)
    assert not any(plan["build"].values())
    publish.publish(source, workspace, "Owner/Repo")
    assert len(state["prs"]) == state["refs"] == 1
    assert "candidate_sha=" + old["headRefOid"] in output.read_text()


def test_retry_does_not_rewrite_drifted_snapshot(source, tmp_path, publishing):
    first_candidate(source, tmp_path, publishing)
    workspace = tmp_path / "retry"
    make_candidate(source, workspace)
    (workspace / "candidate/README.md").write_text("drift")
    with pytest.raises(ValueError, match="refusing overwrite"):
        publish.publish(source, workspace, "Owner/Repo")
    assert publishing[0]["refs"] == 1


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


def test_closed_version_is_never_reopened(source, tmp_path, publishing):
    old = first_candidate(source, tmp_path, publishing)
    old["state"] = "CLOSED"
    workspace = tmp_path / "retry"
    make_candidate(source, workspace)
    with pytest.raises(ValueError, match="closed, merged or changed"):
        publish.publish(source, workspace, "Owner/Repo")
    assert old["state"] == "CLOSED"


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


def test_force_requires_new_run_and_builds_both(source, tmp_path, publishing, monkeypatch):
    state, _ = publishing
    old = first_candidate(source, tmp_path, publishing)
    state["success"].add(old["headRefOid"])
    next_run(monkeypatch)
    plan = make_candidate(source, tmp_path / "rebuild", force=True)
    assert all(plan["build"].values())
    assert all("digest" not in image for image in plan["images"].values())


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


def test_ref_creation_rejects_race_without_creating_pr(source, tmp_path, publishing):
    state, _ = publishing
    workspace = tmp_path / "run"
    make_candidate(source, workspace)
    state["ref_race"] = True
    with pytest.raises(RuntimeError, match="Reference already exists"):
        publish.publish(source, workspace, "Owner/Repo")
    assert not state["prs"]


def test_candidate_reference_change_is_rejected(source, tmp_path, publishing):
    workspace = tmp_path / "run"
    plan = make_candidate(source, workspace)
    git(source, "push", "-q", "origin", "HEAD:refs/heads/" + plan["candidate"]["branch"])
    with pytest.raises(ValueError, match="changed concurrently"):
        publish.publish(source, workspace, "Owner/Repo")
    assert not publishing[0]["prs"]


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


def test_retry_rejects_different_run_id(source, tmp_path, publishing, monkeypatch):
    first_candidate(source, tmp_path, publishing)
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    with pytest.raises(ValueError, match="run identity"):
        publish.prepare(source, tmp_path / "retry", "Owner/Repo")


def test_verification_requires_open_same_repository_pr(source, tmp_path, publishing):
    old = first_candidate(source, tmp_path, publishing)
    old["isCrossRepository"] = True
    with pytest.raises(ValueError, match="same-repository"):
        publish.verify_candidate(source, "Owner/Repo", old["headRefOid"])


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


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "8.8.8.8", "::1"])
def test_ollama_discovery_rejects_nonprivate_target(ip):
    with pytest.raises(ValueError):
        helm_target.private_host(ip)


def test_context_is_always_explicit_and_legacy_ownership_is_not_adopted(tmp_path):
    target = helm_target.Target("second-cluster", "review", tmp_path / "kubeconfig", "node", {})
    assert target.kube[1:5] == [
        "--kubeconfig",
        str(tmp_path / "kubeconfig"),
        "--context",
        "second-cluster",
    ]
    assert target.helm[1:5] == [
        "--kubeconfig",
        str(tmp_path / "kubeconfig"),
        "--kube-context",
        "second-cluster",
    ]
    with pytest.raises(ValueError, match="automatic takeover"):
        helm_deploy.check_ownership(
            {"kind": "Deployment", "metadata": {"name": "review-backend"}},
            "local-review",
            "review",
        )


def test_snapshot_rejects_digest_drift_and_extra_source(source, tmp_path):
    released(source, tmp_path)
    root = tmp_path / "release"
    resources = validate_helm.validate_resources(
        validate_helm.render(root, [root / "release-values.yaml"])
    )
    changed = copy.deepcopy(resources)
    changed["Deployment", "review-backend"]["spec"]["template"]["spec"]["containers"][0][
        "image"
    ] = "backend:latest"
    with pytest.raises(ValueError, match="provenance"):
        validate_helm.validate_snapshot(root, changed)
    (root / "unexpected.py").write_text("print('not deployment content')")
    with pytest.raises(ValueError, match="Unexpected release file"):
        validate_helm.validate_tree(root)


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


@pytest.mark.parametrize(
    "field,value",
    [
        ("branch", "release-candidate/wrong"),
        ("baseSha", "main"),
        ("runNumber", 0),
        ("runId", ""),
        ("version", "0.1.0-rc.2"),
    ],
)
def test_snapshot_rejects_invalid_version_provenance(source, tmp_path, field, value):
    released(source, tmp_path)
    root = tmp_path / "release"
    resources = validate_helm.validate_resources(
        validate_helm.render(root, [root / "release-values.yaml"])
    )
    path = root / "release.json"
    manifest = json.loads(path.read_text())
    manifest["candidate"][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        validate_helm.validate_snapshot(root, resources)


@pytest.mark.parametrize("number,run_id", [("0", "123"), ("01", "123"), ("1", None)])
def test_version_requires_valid_workflow_identity(source, number, run_id):
    with pytest.raises(ValueError, match="Positive GitHub"):
        snapshot.candidate_metadata(source, "a" * 40, number, run_id)
