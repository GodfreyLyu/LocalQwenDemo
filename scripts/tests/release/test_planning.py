"""Release regressions: cumulative inputs, provenance, Helm invariants and target isolation."""

import copy
import json

import pytest
import release.snapshot_impl as snapshot  # noqa: E402
import validation.helm as validate_helm  # noqa: E402

from scripts.tests.support.release import (
    committed,
    git,
    released,
    versioned_plan,
)


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.contract
def test_first_release_builds_both_and_matches_provenance(source, tmp_path):
    manifest = released(source, tmp_path)
    root = tmp_path / "release"
    validate_helm.validate_tree(root)
    resources = validate_helm.render(
        root, [root / "release-values.yaml", root / validate_helm.MINIKUBE_VALUES]
    )
    validate_helm.validate_snapshot(root, validate_helm.validate_resources(resources))
    assert manifest["images"]["backend"]["repository"] == "ghcr.io/owner/repo-backend"
    assert not (root / "backend").exists()


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.contract
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


@pytest.mark.integration
@pytest.mark.requires_git
def test_failed_or_cancelled_intermediate_commit_cannot_hide_changes(source, tmp_path):
    previous = released(source, tmp_path)
    committed(source, "backend/app/main.py", "backend changed")
    # No release from the intermediate commit, then another component changes.
    committed(source, "frontend/main.js", "frontend changed")
    assert snapshot.plan(source, previous, "Owner/Repo")["build"] == {
        "backend": True,
        "frontend": True,
        "ollama": False,
    }


@pytest.mark.integration
@pytest.mark.requires_git
def test_open_candidate_reuses_unchanged_component_source(source, tmp_path):
    previous = released(source, tmp_path)
    committed(source, "frontend/main.js", "frontend changed")
    plan = snapshot.plan(source, previous, "Owner/Repo")
    assert plan["build"] == {"backend": False, "frontend": True, "ollama": False}
    assert plan["images"]["backend"]["sourceSha"] == previous["sourceSha"]
    manifest = snapshot.materialize(
        source,
        tmp_path / "release",
        versioned_plan(source, previous, 2),
        {"frontend": "sha256:" + "c" * 64},
    )
    assert not snapshot.plan(source, manifest, "Owner/Repo")["changed"]


@pytest.mark.integration
@pytest.mark.requires_git
def test_deleted_inputs_and_registry_changes_invalidate_reuse(source, tmp_path):
    previous = released(source, tmp_path)
    (source / "backend/app/main.py").unlink()
    git(source, "add", "--all")
    assert snapshot.plan(source, previous, "Owner/Repo")["build"]["backend"]
    assert all(snapshot.plan(source, previous, "Other/Repo")["build"].values())


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.contract
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


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
def test_standard_helm_snapshot_is_deterministic_and_preserves_image_pins(source, tmp_path):
    released(source, tmp_path)
    root = tmp_path / "release"
    site_values = tmp_path / "site.yaml"
    site_values.write_text(
        "signingSecret:\n  existingSecret: site-signing\n"
        "imagePullSecrets:\n  - name: ghcr-pull\n"
        "config:\n  allowedOrigin: http://localhost:8081\n"
    )
    values = [root / "release-values.yaml", root / validate_helm.MINIKUBE_VALUES, site_values]
    resources = validate_helm.render(root, values)
    assert resources == validate_helm.render(root, values)
    keyed = validate_helm.validate_resources(resources)
    validate_helm.validate_snapshot(root, keyed)
    assert not any(r["kind"] in {"Secret", "NetworkPolicy"} for r in resources)
    assert not any("helm.sh/hook" in r["metadata"].get("annotations", {}) for r in resources)
    pod = keyed["Deployment", "review-backend"]["spec"]["template"]["spec"]
    assert {"secretRef": {"name": "site-signing"}} in pod["containers"][0]["envFrom"]
    assert pod["imagePullSecrets"] == [{"name": "ghcr-pull"}]
    assert keyed["ConfigMap", "review-config"]["data"]["ALLOWED_ORIGIN"] == "http://localhost:8081"
    # The same release is still valid with the explicitly configured policy profile.
    validate_helm.validate_resources(
        validate_helm.render(
            root,
            values,
            [
                "networkPolicy.enabled=true",
            ],
        )
    )


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.contract
def test_snapshot_rejects_digest_drift_and_extra_source(source, tmp_path):
    released(source, tmp_path)
    root = tmp_path / "release"
    resources = validate_helm.validate_resources(
        validate_helm.render(
            root, [root / "release-values.yaml", root / validate_helm.MINIKUBE_VALUES]
        )
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


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.requires_helm
@pytest.mark.contract
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
        validate_helm.render(
            root, [root / "release-values.yaml", root / validate_helm.MINIKUBE_VALUES]
        )
    )
    path = root / "release.json"
    manifest = json.loads(path.read_text())
    manifest["candidate"][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        validate_helm.validate_snapshot(root, resources)


@pytest.mark.integration
@pytest.mark.requires_git
@pytest.mark.contract
@pytest.mark.parametrize("number,run_id", [("0", "123"), ("01", "123"), ("1", None)])
def test_version_requires_valid_workflow_identity(source, number, run_id):
    with pytest.raises(ValueError, match="Positive GitHub"):
        snapshot.candidate_metadata(source, "a" * 40, number, run_id)
