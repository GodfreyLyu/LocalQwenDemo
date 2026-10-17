"""Release inputs, Argo rendering and CI admission boundaries."""

import json

import pytest
import release.snapshot_impl as snapshot
import yaml
from validation.gitops import render_application, validate_snapshot

from scripts.tests.support.paths import ROOT
from scripts.tests.support.release import (
    committed,
    released,
    versioned_plan,
)


@pytest.mark.integration
@pytest.mark.requires_git
def test_ollama_input_change_builds_only_ollama(source, tmp_path):
    previous = released(source, tmp_path)
    committed(source, "deploy/images/ollama-vulkan/runtime.py", "# changed runtime\n")
    plan = snapshot.plan(source, previous, "Owner/Repo")
    assert plan["build"] == {"backend": False, "frontend": False, "ollama": True}
    assert plan["images"]["backend"] == previous["images"]["backend"]


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_backend_change_preserves_ollama_pod_template(source, tmp_path):
    previous = released(source, tmp_path)
    root = tmp_path / "release"
    app = yaml.safe_load((root / snapshot.ARGOCD / "review-ollama-application.yaml").read_text())
    before = render_application(root, app)
    committed(source, "backend/app/main.py", "# new backend\n")
    plan = versioned_plan(source, previous, 2)
    assert plan["build"] == {"backend": True, "frontend": False, "ollama": False}
    manifest = snapshot.materialize(source, root, plan, {"backend": "sha256:" + "e" * 64})
    validate_snapshot(root, manifest)
    assert render_application(root, app) == before


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_clean_snapshot_removes_legacy_executable_files(source, tmp_path):
    root = tmp_path / "release"
    for name in ("scripts/helm_deploy.py", ".github/workflows/old.yml", "docs/old.md"):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("legacy")
    manifest = released(source, tmp_path)
    validate_snapshot(root, manifest)
    assert not (root / ".github").exists()
    assert not (root / "scripts").exists()
    assert not (root / "docs").exists()


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.contract
@pytest.mark.parametrize("change", ["image", "values-order", "destination", "hook"])
def test_gitops_contract_rejects_drift(source, tmp_path, change):
    manifest = released(source, tmp_path)
    root = tmp_path / "release"
    if change in {"values-order", "destination"}:
        path = root / snapshot.ARGOCD / "local-review-application.yaml"
        data = yaml.safe_load(path.read_text())
        if change == "values-order":
            data["spec"]["source"]["helm"]["valueFiles"].reverse()
        else:
            data["spec"]["destination"]["namespace"] = "default"
        path.write_text(yaml.safe_dump(data))
    elif change == "image":
        path = root / snapshot.OLLAMA_CHART / "values-release.yaml"
        data = yaml.safe_load(path.read_text())
        data["image"]["digest"] = "sha256:" + "f" * 64
        path.write_text(yaml.safe_dump(data))
    elif change == "hook":
        path = root / snapshot.OLLAMA_CHART / "templates/gpu-verification-job.yaml"
        path.write_text(path.read_text().replace("PostSync", "PreSync"))
    with pytest.raises(ValueError):
        validate_snapshot(root, manifest)


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.security
@pytest.mark.parametrize(
    "change",
    ["namespace", "wildcard", "secret", "role", "cluster-role", "missing-monitor-kind"],
)
def test_gitops_project_cannot_expand_or_drop_required_access(source, tmp_path, change):
    manifest = released(source, tmp_path)
    root = tmp_path / "release"
    path = root / snapshot.ARGOCD / "project.yaml"
    project = yaml.safe_load(path.read_text())
    spec = project["spec"]
    if change in {"namespace", "wildcard"}:
        spec["destinations"].append(
            {
                "server": "https://kubernetes.default.svc",
                "namespace": "monitoring" if change == "namespace" else "*",
            }
        )
    elif change == "secret":
        spec["namespaceResourceWhitelist"].append({"group": "", "kind": "Secret"})
    elif change == "role":
        spec["namespaceResourceWhitelist"].append(
            {"group": "rbac.authorization.k8s.io", "kind": "Role"}
        )
    elif change == "cluster-role":
        spec["clusterResourceWhitelist"].append(
            {"group": "rbac.authorization.k8s.io", "kind": "ClusterRole"}
        )
    else:
        spec["namespaceResourceWhitelist"] = [
            rule for rule in spec["namespaceResourceWhitelist"] if rule["kind"] != "ServiceMonitor"
        ]
    path.write_text(yaml.safe_dump(project))
    with pytest.raises(ValueError, match="AppProject|cluster-wide"):
        validate_snapshot(root, manifest)


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.security
@pytest.mark.parametrize("chart", [snapshot.CHART, snapshot.OLLAMA_CHART])
@pytest.mark.parametrize("namespace", ["monitoring", "local-inference", None])
def test_gitops_rejects_resources_outside_their_application_namespace(
    source, tmp_path, chart, namespace
):
    manifest = released(source, tmp_path)
    root = tmp_path / "release"
    # Even the other permitted application namespace is outside this release's ownership.
    target = (
        "local-review-demo"
        if chart == snapshot.OLLAMA_CHART and namespace == "local-inference"
        else namespace
    )
    metadata = {"name": "unexpected-config"}
    if target is not None:
        metadata["namespace"] = target
    (root / chart / "templates/unexpected-config.yaml").write_text(
        yaml.safe_dump({"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata})
    )
    with pytest.raises(ValueError, match="Application namespace"):
        validate_snapshot(root, manifest)


@pytest.mark.integration
def test_pr_quality_is_required_before_main_image_publication():
    # BaseLoader keeps GitHub's YAML 'on' key and boolean-looking expressions as strings.
    quality = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert quality["on"]["pull_request"]["branches"] == ["main"]
    gate = quality["jobs"]["main-ci"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"application", "configuration"}
    release = yaml.load(
        (ROOT / ".github/workflows/release-candidate.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert release["on"]["push"]["branches"] == ["main"]
    assert "pull_request" not in release["on"]
    assert release["jobs"]["candidate"]["needs"] == "quality"
    assert release["jobs"]["quality"]["if"] == "github.ref == 'refs/heads/main'"
    protection = json.loads((ROOT / "deploy/release/main-protection.json").read_text())
    assert protection["required_status_checks"]["strict"]
    assert protection["required_status_checks"]["checks"] == [
        {"context": "main-ci", "app_id": 15368}
    ]
    assert protection["enforce_admins"]
    assert protection["required_pull_request_reviews"]["required_approving_review_count"] == 0


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_review_model_is_independent_from_bootstrap_model(source, tmp_path):
    manifest = released(source, tmp_path)
    root = tmp_path / "release"
    path = root / snapshot.OLLAMA_CHART / "values.yaml"
    path.write_text(path.read_text().replace("qwen3:1.7b", "qwen3:other"))
    validate_snapshot(root, manifest)
