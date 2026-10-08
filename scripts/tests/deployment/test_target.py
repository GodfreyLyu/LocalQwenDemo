"""Explicit target selection and Helm ownership."""

import deployment.common.target as helm_target
import pytest


@pytest.mark.integration
@pytest.mark.security
def test_context_is_always_explicit_and_foreign_ownership_is_not_adopted(tmp_path):
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
        helm_target.check_ownership(
            {"kind": "Deployment", "metadata": {"name": "review-backend"}},
            "local-review",
            "review",
        )
