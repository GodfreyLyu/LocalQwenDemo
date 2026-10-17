"""Monitoring discovery invariants and offline Prometheus validation."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from validation.resources import require


def validate_monitoring(resources):
    keyed = {(item["kind"], item["metadata"]["name"]): item for item in resources}
    monitor = keyed.get(("ServiceMonitor", "review-backend"))
    if monitor is None:
        require(not any(r["kind"] == "PrometheusRule" for r in resources), "Monitor is missing")
        return
    namespace = monitor["metadata"]["namespace"]
    require(monitor["apiVersion"] == "monitoring.coreos.com/v1", "Unexpected monitor API")
    require(
        monitor["spec"]["namespaceSelector"] == {"matchNames": [namespace]},
        "Monitoring must target the application namespace",
    )
    selector = monitor["spec"]["selector"]["matchLabels"]
    services = [
        item
        for item in resources
        if item["kind"] == "Service"
        and selector.items() <= item["metadata"].get("labels", {}).items()
    ]
    require(
        [item["metadata"]["name"] for item in services] == ["review-backend"],
        "ServiceMonitor must select only the backend Service",
    )
    endpoints = monitor["spec"]["endpoints"]
    require(
        len(endpoints) == 1
        and endpoints[0]["path"] == "/metrics"
        and endpoints[0]["port"] == "http"
        and endpoints[0]["honorLabels"] is False,
        "Unexpected metrics endpoint",
    )
    require(
        any(
            port["name"] == "http" and port["port"] == 8000 for port in services[0]["spec"]["ports"]
        ),
        "Metrics endpoint must resolve to the backend port",
    )
    release = monitor["metadata"]["labels"]["release"]
    require(bool(release), "Prometheus discovery label must be explicit")
    for resource in resources:
        if resource["kind"] == "PrometheusRule":
            require(
                resource["apiVersion"] == "monitoring.coreos.com/v1"
                and resource["metadata"]["namespace"] == namespace
                and resource["metadata"]["labels"].get("release") == release,
                "Rules must share the monitor namespace and discovery label",
            )
        if (
            resource["kind"] == "ConfigMap"
            and resource["metadata"].get("labels", {}).get("grafana_dashboard") == "1"
        ):
            require(resource["metadata"]["namespace"] == namespace, "Dashboard namespace mismatch")
            for content in resource["data"].values():
                dashboard = json.loads(content)
                require(bool(dashboard.get("uid")), "Dashboard must have a stable UID")
    policy = keyed.get(("NetworkPolicy", "review-backend"))
    if policy:
        sources = [source for rule in policy["spec"]["ingress"] for source in rule["from"]]
        external = [source for source in sources if "namespaceSelector" in source]
        require(
            len(external) == 1
            and bool(external[0]["namespaceSelector"].get("matchLabels"))
            and bool(external[0].get("podSelector", {}).get("matchLabels")),
            "Prometheus access must constrain namespace and Pod in the same source",
        )


def check_prometheus(resources, promtool, fixture):
    """Validate actual rendered rules, their scenarios, and dashboard expressions."""
    validate_monitoring(resources)
    groups = [
        group
        for resource in resources
        if resource["kind"] == "PrometheusRule"
        for group in resource["spec"]["groups"]
    ]
    require(bool(groups), "Monitoring rules are missing")
    with tempfile.TemporaryDirectory(prefix="review-monitoring-") as directory:
        root = Path(directory)
        rules = root / "rules.yaml"
        rules.write_text(yaml.safe_dump({"groups": groups}))
        shutil.copyfile(fixture, root / "rules.test.yaml")
        subprocess.run([promtool, "check", "rules", str(rules)], check=True)
        subprocess.run([promtool, "test", "rules", "rules.test.yaml"], cwd=root, check=True)
    expressions = []
    for resource in resources:
        if (
            resource["kind"] != "ConfigMap"
            or resource["metadata"].get("labels", {}).get("grafana_dashboard") != "1"
        ):
            continue
        for content in resource["data"].values():
            dashboard = json.loads(content)
            for panel in dashboard["panels"]:
                for target in panel.get("targets", []):
                    expression = target["expr"]
                    for variable, value in {
                        "$namespace": "local-review-demo",
                        "$ollama_namespace": "local-inference",
                        "$ollama_release": "review-ollama",
                        "$__rate_interval": "5m",
                        "$__range": "1h",
                    }.items():
                        expression = expression.replace(variable, value)
                    expressions.append(
                        {"record": f"test_dashboard_{len(expressions)}", "expr": expression}
                    )
    require(bool(expressions), "Dashboard expressions are missing")
    with tempfile.TemporaryDirectory(prefix="review-dashboard-") as directory:
        queries = Path(directory) / "queries.yaml"
        queries.write_text(
            yaml.safe_dump({"groups": [{"name": "dashboard-queries", "rules": expressions}]})
        )
        subprocess.run([promtool, "check", "rules", str(queries)], check=True)
