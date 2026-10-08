from pathlib import Path

CHART = Path("deploy/helm/local-review")

MINIKUBE_VALUES = CHART / "values-minikube.yaml"
