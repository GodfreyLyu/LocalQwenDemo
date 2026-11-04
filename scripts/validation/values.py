"""Read approved snapshot values without rewriting historical rollback artifacts."""

import json

import yaml

from validation.constants import CHART
from validation.resources import require

RELEASE_VALUES = ["values-release.yaml"]
# Exact historical ordering is retained only to validate archived rollback snapshots.
LEGACY_RELEASE_VALUES = [
    "values-release.yaml",
    "values-minikube.yaml",
    "values-minikube-ollama.yaml",
    "values-monitoring.yaml",
]


def application_value_names(root):
    """Accept only the current composition or the exact archived format-3 composition."""
    application = yaml.safe_load((root / "deploy/argocd/local-review-application.yaml").read_text())
    names = application["spec"]["source"]["helm"]["valueFiles"]
    require(
        names in (RELEASE_VALUES, LEGACY_RELEASE_VALUES),
        "Unexpected Argo source or value precedence",
    )
    return names


def snapshot_values(root):
    """Use a snapshot's saved configuration, including supported historical Helm releases."""
    manifest = json.loads((root / "release.json").read_text())
    require(manifest.get("schemaVersion") in {1, 2, 3}, "Unknown release manifest format")
    if manifest["schemaVersion"] == 3:
        return [root / CHART / name for name in application_value_names(root)]
    values = [root / "release-values.yaml"]
    # Format-1/2 snapshots predate Argo CD and used this environment file with Helm.
    environment = root / CHART / "values-minikube.yaml"
    if environment.exists():
        values.append(environment)
    return values
