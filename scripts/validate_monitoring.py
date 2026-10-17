"""Validate rendered monitoring rules and dashboards without a cluster or inference."""

import argparse
import subprocess
from pathlib import Path

import yaml
from tooling_paths import ROOT
from validation.monitoring import check_prometheus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--promtool", default="promtool")
    args = parser.parse_args()
    if "/" in args.promtool:
        args.promtool = str(Path(args.promtool).resolve())
    chart = args.root.resolve() / "deploy/helm/local-review"
    command = ["helm", "template", "local-review", str(chart), "-n", "local-review-demo"]
    if (chart / "values-release.yaml").exists():
        command.extend(["-f", str(chart / "values-release.yaml")])
    for name in ("values-minikube.yaml", "values-minikube-ollama.yaml", "values-monitoring.yaml"):
        command.extend(["-f", str(chart / name)])
    resources = list(yaml.safe_load_all(subprocess.check_output(command, text=True)))
    check_prometheus(resources, args.promtool, ROOT / "scripts/tests/monitoring/rules.test.yaml")
    print("Monitoring rules, scenarios, and dashboard expressions validated.")


if __name__ == "__main__":
    main()
