"""Release inputs, Argo rendering and CI admission boundaries."""

import json

import pytest
import yaml
from test_helm_release import (
    ROOT,
    committed,
    released,
    snapshot,
    versioned_plan,
)
from test_helm_release import source as source_fixture
from validate_gitops import render_application, validate_snapshot


@pytest.fixture
def source(tmp_path):
    return source_fixture.__wrapped__(tmp_path)


def test_ollama_input_change_builds_only_ollama(source, tmp_path):
    previous = released(source, tmp_path)
    committed(source, "deploy/images/ollama-vulkan/runtime.py", "# changed runtime\n")
    plan = snapshot.plan(source, previous, "Owner/Repo")
    assert plan["build"] == {"backend": False, "frontend": False, "ollama": True}
    assert plan["images"]["backend"] == previous["images"]["backend"]


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


@pytest.mark.parametrize("change", ["image", "values-order", "destination", "hook", "model"])
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
    else:
        path = root / snapshot.OLLAMA_CHART / "values.yaml"
        path.write_text(path.read_text().replace("qwen3:1.7b", "qwen3:other"))
    with pytest.raises(ValueError):
        validate_snapshot(root, manifest)


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
