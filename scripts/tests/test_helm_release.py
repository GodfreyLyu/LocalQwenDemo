"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import copy
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


def released(root, tmp_path):
    plan = snapshot.plan(root, {}, "Owner/Repo")
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
        source, tmp_path / "release", plan, {"frontend": "sha256:" + "c" * 64}
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
    import json

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
    original = publish.command
    state = {"open": False, "creates": 0, "updates": 0}

    def fake_github(*args, **kwargs):
        if args[:3] == ("gh", "pr", "list"):
            sha = publish.remote(source, publish.HEAD)
            return json.dumps([{"number": 1, "headRefOid": sha}] if state["open"] else [])
        if args[:3] == ("gh", "pr", "create"):
            state["creates"] += 1
            state["open"] = True
            return "https://example.test/pr/1"
        if args[:3] == ("gh", "pr", "edit"):
            state["updates"] += 1
            return ""
        return original(*args, **kwargs)

    monkeypatch.setattr(publish, "command", fake_github)
    return state, output


def make_candidate(source, workspace):
    import json

    publish.prepare(source, workspace, "Owner/Repo")
    plan = json.loads((workspace / "plan.json").read_text())
    snapshot.materialize(
        source,
        workspace / "candidate",
        plan,
        {
            "backend": "sha256:" + "a" * 64,
            "frontend": "sha256:" + "b" * 64,
        },
    )
    return plan


def test_publishing_updates_one_pr_and_revalidates_noop(source, tmp_path, publishing):
    state, output = publishing
    first = tmp_path / "first"
    make_candidate(source, first)
    publish.publish(source, first, "Owner/Repo")
    assert state["creates"] == 1
    committed(source, "frontend/main.js", "frontend v2")
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    second = tmp_path / "second"
    plan = make_candidate(source, second)
    assert plan["build"] == {"backend": False, "frontend": True}
    publish.publish(source, second, "Owner/Repo")
    assert state["updates"] == 1 and state["creates"] == 1
    output.write_text("")
    publish.prepare(source, tmp_path / "third", "Owner/Repo")
    assert "changed=false" in output.read_text()
    assert "existing_candidate_sha=" + publish.remote(source, publish.HEAD) in output.read_text()


def test_stale_main_and_changed_release_base_are_not_published(source, tmp_path, publishing):
    state, output = publishing
    workspace = tmp_path / "candidate-run"
    make_candidate(source, workspace)
    committed(source, "frontend/main.js", "newer main")
    git(source, "push", "-q", "origin", "HEAD:refs/heads/main")
    publish.publish(source, workspace, "Owner/Repo")
    assert state["creates"] == 0 and "published=false" in output.read_text()
    workspace = tmp_path / "second-run"
    make_candidate(source, workspace)
    git(source, "push", "-q", "--force", "origin", "HEAD:refs/heads/deployment-release")
    with pytest.raises(ValueError, match="Release base changed"):
        publish.publish(source, workspace, "Owner/Repo")


def test_candidate_lease_rejects_concurrent_branch_change(source, tmp_path, publishing):
    state, _ = publishing
    workspace = tmp_path / "candidate-run"
    make_candidate(source, workspace)
    git(source, "push", "-q", "origin", "HEAD:refs/heads/automation/deployment-release")
    with pytest.raises(RuntimeError, match="git push failed"):
        publish.publish(source, workspace, "Owner/Repo")
    assert state["creates"] == 0


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
