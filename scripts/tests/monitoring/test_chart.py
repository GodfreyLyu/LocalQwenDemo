"""Monitor discovery, access boundaries, dashboard queries, and alert behavior."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.tests.support.paths import ROOT

CHART = ROOT / "deploy/helm/local-review"
VALUES = CHART / "values-monitoring.yaml"
pytestmark = [pytest.mark.integration, pytest.mark.requires_helm, pytest.mark.contract]


def render(*settings, monitoring=True):
    command = ["helm", "template", "local-review", str(CHART), "--namespace", "local-review-demo"]
    if monitoring:
        command += ["-f", str(VALUES)]
    for setting in settings:
        command += ["--set", setting]
    return {
        (resource["kind"], resource["metadata"]["name"]): resource
        for resource in yaml.safe_load_all(subprocess.check_output(command, text=True))
        if resource
    }


@pytest.fixture(scope="module")
def resources():
    return render()


def test_monitoring_is_opt_in_and_its_parts_can_be_disabled(resources):
    disabled = render(monitoring=False)
    assert not any(kind in {"ServiceMonitor", "PrometheusRule"} for kind, _ in disabled)
    assert not any(name.startswith("review-service-overview") for _, name in disabled)
    selected = render("monitoring.alerts.enabled=false", "monitoring.dashboards.enabled=false")
    assert ("ServiceMonitor", "review-backend") in selected
    assert ("PrometheusRule", "review-backend") not in selected
    assert ("ConfigMap", "review-service-overview") not in selected
    assert ("ConfigMap", "review-inference-resources") not in selected
    assert ("PrometheusRule", "review-backend") in resources


@pytest.mark.security
def test_only_backend_service_is_scraped_and_frontend_has_no_metrics_proxy(resources):
    monitor = resources["ServiceMonitor", "review-backend"]
    assert monitor["metadata"]["labels"]["release"] == "monitoring"
    assert monitor["spec"]["namespaceSelector"] == {"matchNames": ["local-review-demo"]}
    labels = monitor["spec"]["selector"]["matchLabels"]
    matches = [
        name
        for (kind, name), resource in resources.items()
        if kind == "Service" and resource["metadata"]["labels"].items() >= labels.items()
    ]
    assert matches == ["review-backend"]
    assert monitor["spec"]["endpoints"] == [
        {
            "port": "http",
            "path": "/metrics",
            "interval": "30s",
            "scrapeTimeout": "10s",
            "honorLabels": False,
        }
    ]
    assert monitor["spec"]["sampleLimit"] == 5000
    nginx = resources["ConfigMap", "review-local-nginx"]["data"]["nginx.conf"]
    assert "/metrics" not in nginx


@pytest.mark.security
def test_metrics_network_access_requires_both_namespace_and_prometheus_pod(resources):
    ingress = resources["NetworkPolicy", "review-backend"]["spec"]["ingress"]
    assert len(ingress) == 2
    assert ingress[1] == {
        "from": [
            {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": "prometheus",
                        "prometheus": "monitoring-kube-prometheus-prometheus",
                    }
                },
            }
        ],
        "ports": [{"port": 8000, "protocol": "TCP"}],
    }
    local = render("networkPolicy.enabled=false")
    assert not any(kind == "NetworkPolicy" for kind, _ in local)


@pytest.mark.parametrize(
    "setting",
    [
        "monitoring.unknown=true",
        "monitoring.intervalSeconds=0",
        "monitoring.scrapeTimeoutSeconds=31",
        "monitoring.sampleLimit=0",
        "monitoring.alerts.minimumCompletedReviews=1",
        "monitoring.alerts.failureRatio=2",
        "monitoring.namespace=invalid_namespace",
        "monitoring.release=invalid_release",
    ],
)
def test_invalid_monitoring_settings_are_rejected(setting):
    result = subprocess.run(
        ["helm", "template", "test", str(CHART), "-f", str(VALUES), "--set", setting],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "monitoring" in result.stderr


def test_alert_startup_allowance_and_queue_budget_follow_runtime_settings():
    resources = render("startupTimeoutSeconds=600", "model.timeoutSeconds=60")
    rules = {
        rule["alert"]: rule
        for group in resources["PrometheusRule", "review-backend"]["spec"]["groups"]
        for rule in group["rules"]
    }
    assert rules["ReviewMetricsTargetMissing"]["for"] == "720s"
    assert rules["ReviewCoordinatorUnavailable"]["for"] == "720s"
    assert "for" not in rules["ReviewInferenceStuck"]
    assert "max_over_time(" in rules["ReviewInferenceStuck"]["expr"]
    assert "[5m]" in rules["ReviewInferenceStuck"]["expr"]
    assert "* 60)" in rules["ReviewQueueBacklog"]["expr"]


def dashboards(resources):
    return [
        json.loads(next(iter(resources["ConfigMap", name]["data"].values())))
        for name in ("review-service-overview", "review-inference-resources")
    ]


def test_dashboard_discovery_datasource_and_measurement_scopes(resources):
    boards = dashboards(resources)
    assert len({board["uid"] for board in boards}) == 2
    for board in boards:
        assert len(board["uid"]) <= 40
        assert board["templating"]["list"][0]["current"]["value"] == "prometheus"
        assert board["templating"]["list"][1]["current"]["value"] == "local-review-demo"
        assert "__REVIEW_NAMESPACE__" not in json.dumps(board)
        for panel in board["panels"]:
            for target in panel.get("targets", []):
                assert target["datasource"] == {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
                assert " or vector(0)" not in target["expr"]
                if panel["type"] == "stat":
                    assert target["instant"] and not target["range"]
    for name in ("review-service-overview", "review-inference-resources"):
        assert resources["ConfigMap", name]["metadata"]["labels"]["grafana_dashboard"] == "1"
    inference = boards[1]
    assert "GPU utilization is **not measured**" in inference["panels"][0]["options"]["content"]
    cpu = next(panel for panel in inference["panels"] if panel["title"] == "Container CPU usage")
    assert 'container="review-backend"' in cpu["targets"][0]["expr"]
    assert 'container="ollama"' in cpu["targets"][1]["expr"]
    for panel in boards[0]["panels"]:
        if panel["title"].startswith("HTTP"):
            assert all('route!~"/health/.*"' in target["expr"] for target in panel["targets"])


def dashboard_expression(expression):
    for variable, value in {
        "$namespace": "local-review-demo",
        "$ollama_namespace": "local-inference",
        "$ollama_release": "review-ollama",
        "$__rate_interval": "2m",
    }.items():
        expression = expression.replace(variable, value)
    return expression


def test_promtool_alert_behavior_and_dashboard_query_syntax(resources, tmp_path):
    executable = shutil.which(os.environ.get("PROMTOOL", "promtool"))
    if not executable:
        pytest.skip("Set PROMTOOL or install promtool to validate alert and dashboard PromQL")
    rules = resources["PrometheusRule", "review-backend"]["spec"]
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump(rules))
    fixture = Path(__file__).with_name("rules.test.yaml")
    scenarios = yaml.safe_load(fixture.read_text())
    panels = {panel["title"]: panel for panel in dashboards(resources)[0]["panels"]}
    http = panels["HTTP requests by status · excluding probes"]["targets"][0]["expr"]
    duration = panels["Review duration · p50 and p95"]["targets"][0]["expr"]
    labels = 'namespace="local-review-demo",service="review-backend"'
    scenarios["tests"].extend(
        [
            {
                "name": "dashboard HTTP rate excludes health probes but keeps unknown route errors",
                "interval": "1m",
                "input_series": [
                    {
                        "series": f'review_http_requests_total{{{labels},route="/health/live",'
                        'status="200",method="GET"}',
                        "values": "0+10x5",
                    },
                    {
                        "series": f'review_http_requests_total{{{labels},route="/api/v1/reviews",'
                        'status="200",method="GET"}',
                        "values": "0+1x5",
                    },
                    {
                        "series": f'review_http_requests_total{{{labels},route="unknown",'
                        'status="400",method="POST"}',
                        "values": "0+1x5",
                    },
                ],
                "promql_expr_test": [
                    {
                        "expr": f"round(60 * ({dashboard_expression(http)}))",
                        "eval_time": "5m",
                        "exp_samples": [
                            {"labels": '{status="200"}', "value": 1},
                            {"labels": '{status="400"}', "value": 1},
                        ],
                    }
                ],
            },
            {
                "name": "dashboard quantiles stay absent until the histogram has observations",
                "interval": "1m",
                "input_series": [
                    {
                        "series": f'review_duration_seconds_bucket{{{labels},le="1"}}',
                        "values": "0x5",
                    },
                    {
                        "series": f'review_duration_seconds_bucket{{{labels},le="+Inf"}}',
                        "values": "0x5",
                    },
                    {"series": f"review_duration_seconds_count{{{labels}}}", "values": "0x5"},
                ],
                "promql_expr_test": [
                    {
                        "expr": dashboard_expression(duration),
                        "eval_time": "5m",
                        "exp_samples": [],
                    }
                ],
            },
        ]
    )
    (tmp_path / fixture.name).write_text(yaml.safe_dump(scenarios))
    subprocess.run([executable, "check", "rules", "rules.yaml"], cwd=tmp_path, check=True)
    subprocess.run([executable, "test", "rules", fixture.name], cwd=tmp_path, check=True)
    expressions = []
    for board in dashboards(resources):
        for panel in board["panels"]:
            for target in panel.get("targets", []):
                expression = dashboard_expression(target["expr"])
                expressions.append(
                    {
                        "record": f"test_dashboard_{len(expressions)}",
                        "expr": expression,
                    }
                )
    (tmp_path / "dashboard-queries.yaml").write_text(
        yaml.safe_dump({"groups": [{"name": "dashboard-queries", "rules": expressions}]})
    )
    subprocess.run(
        [executable, "check", "rules", "dashboard-queries.yaml"],
        cwd=tmp_path,
        check=True,
    )
