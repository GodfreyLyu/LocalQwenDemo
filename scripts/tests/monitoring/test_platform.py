"""Offline platform ownership, discovery, credential boundaries and local resource budget."""

import re

import pytest
import yaml
from deployment.common.resources import parse_quantity

from scripts.tests.support.paths import ROOT

PLATFORM = ROOT / "infra/local-platform/terraform/platform"
pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def values():
    return yaml.safe_load((PLATFORM / "values/monitoring.yaml").read_text())


def variable(name):
    source = (PLATFORM / "variables.tf").read_text()
    match = re.search(rf'(?ms)^variable "{name}" \{{\n(.*?)^\}}', source)
    assert match is not None
    return match[1]


def test_platform_installation_is_optional_pinned_and_depends_on_its_namespace():
    assert re.search(r"\bdefault\s*=\s*false\b", variable("monitoring_enabled"))
    assert re.search(r'\bdefault\s*=\s*"92\.10\.1"', variable("monitoring_chart_version"))
    release = (PLATFORM / "monitoring.tf").read_text()
    for expression in (
        r'resource "helm_release" "monitoring"',
        r"count\s*=\s*var\.monitoring_enabled\s*\?\s*1\s*:\s*0",
        r'name\s*=\s*"monitoring"',
        r'namespace\s*=\s*"monitoring"',
        r'repository\s*=\s*"https://prometheus-community\.github\.io/helm-charts"',
        r'chart\s*=\s*"kube-prometheus-stack"',
        r"version\s*=\s*var\.monitoring_chart_version",
        r'values\s*=\s*\[file\("\$\{path.module\}/values/monitoring.yaml"\)\]',
        r"depends_on\s*=\s*\[kubernetes_namespace_v1\.platform\]",
        r'condition\s*=\s*contains\(var\.namespaces, "monitoring"\)',
    ):
        assert re.search(expression, release), expression
    assert '"monitoring"' in variable("namespaces")
    assert (
        'resource "kubernetes_namespace_v1" "platform"' in (PLATFORM / "namespaces.tf").read_text()
    )
    assert "create_namespace" not in release
    assert "kubernetes_secret" not in release
    example = (PLATFORM / "terraform.tfvars.example").read_text()
    assert re.search(r"monitoring_enabled\s*=\s*false\b", example)


def test_scrape_and_rule_discovery_select_only_the_platform_and_application(values):
    prometheus = values["prometheus"]["prometheusSpec"]
    application = yaml.safe_load((ROOT / "deploy/helm/local-review/values.yaml").read_text())
    release = application["monitoring"]["release"]
    assert release == "monitoring"
    assert application["monitoring"]["namespace"] == "monitoring"
    for kind in ("serviceMonitor", "podMonitor", "rule"):
        assert prometheus[kind + "Selector"] == {"matchLabels": {"release": release}}
        assert prometheus[kind + "NamespaceSelector"] == {
            "matchExpressions": [
                {
                    "key": "kubernetes.io/metadata.name",
                    "operator": "In",
                    "values": ["monitoring", "local-review-demo"],
                }
            ]
        }
    for kind in ("probe", "scrapeConfig"):
        assert prometheus[kind + "Selector"] == {"matchLabels": {"release": release}}
        assert prometheus[kind + "NamespaceSelector"] == {
            "matchLabels": {"kubernetes.io/metadata.name": "monitoring"}
        }


@pytest.mark.security
def test_grafana_credentials_stay_external_and_sidecars_only_read_configmaps(values):
    grafana = values["grafana"]
    assert grafana["admin"] == {
        "existingSecret": "monitoring-grafana-admin",
        "userKey": "admin-user",
        "passwordKey": "admin-password",
    }
    assert "adminPassword" not in grafana
    assert "GF_SECURITY_ADMIN_PASSWORD" not in grafana.get("env", {})
    assert grafana["rbac"] == {
        "namespaced": True,
        "namespaces": ["local-review-demo"],
        "useExistingRole": "monitoring-grafana-configmaps",
    }
    roles = grafana["extraObjects"]
    assert len(roles) == 2
    assert {role["metadata"]["namespace"] for role in roles} == {
        "monitoring",
        "local-review-demo",
    }
    for role in roles:
        assert role["kind"] == "Role"
        assert role["metadata"]["name"] == grafana["rbac"]["useExistingRole"]
        assert role["rules"] == [
            {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "list", "watch"]}
        ]
    dashboards = grafana["sidecar"]["dashboards"]
    assert dashboards["searchNamespace"] == ["monitoring", "local-review-demo"]
    assert dashboards["resource"] == "configmap"
    assert (dashboards["label"], dashboards["labelValue"]) == ("grafana_dashboard", "1")
    assert grafana["sidecar"]["datasources"] == {
        "resource": "configmap",
        "searchNamespace": "monitoring",
    }
    for component in ("grafana", "prometheus", "alertmanager"):
        assert values[component]["service"]["type"] == "ClusterIP"
        assert not values[component].get("ingress", {}).get("enabled", False)


def test_retention_and_resource_budgets_are_bounded(values):
    prometheus = values["prometheus"]["prometheusSpec"]
    assert prometheus["replicas"] == 1
    assert prometheus["scrapeInterval"] == prometheus["evaluationInterval"] == "30s"
    assert prometheus["retention"] == "3d"
    assert prometheus["retentionSize"] == "4GiB"
    assert prometheus["storageSpec"]["volumeClaimTemplate"]["spec"] == {
        "storageClassName": "standard",
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "5Gi"}},
    }
    assert values["alertmanager"]["alertmanagerSpec"]["replicas"] == 1
    assert values["grafana"]["persistence"]["enabled"] is False
    operator = values["prometheusOperator"]
    budgets = [
        prometheus["resources"],
        values["alertmanager"]["alertmanagerSpec"]["resources"],
        values["grafana"]["resources"],
        values["kube-state-metrics"]["resources"],
        values["prometheus-node-exporter"]["resources"],
        operator["resources"],
        *[values["grafana"]["sidecar"]["resources"]] * 2,
        *[operator["prometheusConfigReloader"]["resources"]] * 2,
    ]
    # Both sidecars and both reloaders run continuously; patch jobs are transient.
    for budget in budgets + [operator["admissionWebhooks"]["patch"]["resources"]]:
        for resource in ("cpu", "memory"):
            request = parse_quantity(budget["requests"][resource])
            limit = parse_quantity(budget["limits"][resource])
            assert 0 < request <= limit
    assert sum(parse_quantity(b["requests"]["cpu"]) for b in budgets) <= 0.5
    assert sum(parse_quantity(b["limits"]["cpu"]) for b in budgets) <= 3
    assert sum(parse_quantity(b["requests"]["memory"]) for b in budgets) <= 1.25 * 1024**3
    assert sum(parse_quantity(b["limits"]["memory"]) for b in budgets) <= 3 * 1024**3


def test_only_unexposed_control_plane_targets_and_related_rules_are_disabled(values):
    for target in ("kubeEtcd", "kubeControllerManager", "kubeProxy", "kubeScheduler"):
        assert values[target]["enabled"] is False
    assert values["defaultRules"]["rules"] == {
        "etcd": False,
        "kubeControllerManager": False,
        "kubeProxy": False,
        "kubeSchedulerAlerting": False,
        "kubeSchedulerRecording": False,
    }
    for target in ("kubeApiServer", "kubelet", "kubeStateMetrics", "nodeExporter"):
        assert values.get(target, {}).get("enabled", True) is True
    assert values["defaultRules"].get("create", True) is True
    assert not values["defaultRules"].get("disabled")
