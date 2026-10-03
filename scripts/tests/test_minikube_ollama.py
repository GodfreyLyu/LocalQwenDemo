"""Offline address-discovery and real-render regressions; no cluster or model calls."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minikube_demo as d  # noqa: E402
import minikube_ollama as network  # noqa: E402


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr(d, "PROFILE", "selected-profile")
    monkeypatch.setattr(d, "TARGET", {"node_name": "selected-node"})
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="172.19.0.1 STREAM host\n"))
    monkeypatch.setattr(d, "run", run)
    return run


def test_discovery_uses_selected_node_and_deduplicates_getent_records(resolver):
    resolver.return_value.stdout = (
        "172.19.0.1 STREAM host.minikube.internal\n172.19.0.1 DGRAM\n172.19.0.1 RAW\n"
    )
    assert network.resolve_host_ip() == "172.19.0.1"
    resolver.assert_called_once_with(
        ["docker", "exec", "selected-node", "getent", "ahostsv4", network.HOSTNAME],
        check=False,
        timeout=15,
    )


@pytest.mark.parametrize("value", ["10.0.2.2", "172.16.0.1", "172.31.255.254", "192.168.80.254"])
def test_private_host_addresses_generate_only_one_host_and_port(value):
    assert network.egress_rule(value) == {
        "to": [{"ipBlock": {"cidr": value + "/32"}}],
        "ports": [{"protocol": "TCP", "port": 11434}],
    }


@pytest.mark.parametrize(
    "value",
    [
        "",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "8.8.8.8",
        "172.32.0.1",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fd00::1",
        "192.168.1.0/24",
        "$(secret)",
    ],
)
def test_bad_addresses_never_render_an_allowance(value):
    with pytest.raises(d.DemoError):
        network.egress_rule(value)


@pytest.mark.parametrize(
    "output,code",
    [
        ("", 0),
        (" \n", 0),
        ("172.19.0.1 STREAM host", 2),
        ("172.19.0.1 STREAM\n172.19.0.2 STREAM", 0),
        ("172.19.0.1 STREAM\n127.0.0.1 STREAM", 0),
        ("fd00::1 STREAM", 0),
        ("sensitive-invalid-output", 0),
    ],
)
def test_failed_or_ambiguous_resolution_stops_without_fallback(resolver, output, code):
    resolver.return_value = SimpleNamespace(stdout=output, returncode=code)
    with pytest.raises(d.DemoError) as error:
        network.resolve_host_ip()
    assert "sensitive-invalid-output" not in str(error.value)


def test_resolver_timeout_is_not_retried_or_cached(resolver):
    resolver.side_effect = d.DemoError("docker unavailable or timed out")
    with pytest.raises(d.DemoError, match="timed out"):
        network.resolve_host_ip()
    assert resolver.call_count == 1


def test_missing_target_stops_before_docker(monkeypatch, resolver):
    monkeypatch.setattr(d, "TARGET", {})
    with pytest.raises(d.DemoError, match="verified minikube node"):
        network.resolve_host_ip()
    resolver.assert_not_called()


def backend_policy(resources):
    return next(
        r
        for r in resources
        if r["kind"] == "NetworkPolicy" and r["metadata"]["name"] == "review-backend"
    )


def test_real_render_replaces_environment_value_and_preserves_other_resources(monkeypatch):
    # Offline renders must not implicitly resolve names, even for ownership checks.
    monkeypatch.setattr(
        network, "resolve_host_ip", Mock(side_effect=AssertionError("live discovery"))
    )
    baseline = d.render(8080)
    for address in ("192.168.80.254", "172.19.0.1", "192.168.80.254"):
        rendered = d.render(8080, ollama_host_ip=address)
        expected = copy.deepcopy(baseline)
        backend_policy(expected)["spec"]["egress"].append(network.egress_rule(address))
        assert rendered == expected
        rules = backend_policy(rendered)["spec"]["egress"]
        assert len([r for r in rules if {"protocol": "TCP", "port": 11434} in r["ports"]]) == 1
    assert "11434" not in json.dumps(backend_policy(baseline))
    assert "192.168.65.254" not in json.dumps(baseline)


def test_resolution_failure_precedes_namespace_build_and_application_mutations(monkeypatch):
    monkeypatch.setattr(d, "require_local_docker", lambda: None)
    monkeypatch.setattr(d, "state", lambda **kw: None)
    monkeypatch.setattr(d, "check_ownership", lambda _: None)
    monkeypatch.setattr(network, "resolve_host_ip", Mock(side_effect=d.DemoError("no host")))
    save, kubectl, build = Mock(), Mock(), Mock()
    monkeypatch.setattr(d, "save", save)
    monkeypatch.setattr(d, "k", kubectl)
    monkeypatch.setattr(d, "check_images", build)
    stages = []
    with pytest.raises(d.DemoError, match="no host"):
        d.deploy_application(SimpleNamespace(), {"architecture": "arm64"}, stages.append)
    assert stages[-1] == "ollama_host_resolution"
    save.assert_not_called()
    kubectl.assert_not_called()
    build.assert_not_called()


@pytest.mark.parametrize("backend", ["ollama", "transformers"])
def test_explicit_adapter_render_preserves_budget_and_network(backend):
    resources = d.render(8080, ollama_host_ip="192.168.65.254", model_backend=backend)
    config = next(
        r["data"]
        for r in resources
        if r["kind"] == "ConfigMap" and r["metadata"]["name"] == "review-config"
    )
    assert config["MODEL_BACKEND"] == backend
    assert config["MODEL_MAX_INPUT_TOKENS"] == "2048"
    assert config["MODEL_MAX_OUTPUT_TOKENS"] == "384"
    assert config["OLLAMA_BASE_URL"] == "http://host.minikube.internal:11434"
    assert config["OLLAMA_MODEL_DIGEST"].startswith("sha256:")
    assert network.egress_rule("192.168.65.254") in backend_policy(resources)["spec"]["egress"]


def test_acceptance_uses_ollama_digest_and_rejects_hf_revision():
    import minikube_verify as v

    identity = {"model_id": "qwen3:1.7b", "model_revision": "sha256:" + "a" * 64}
    review = identity | {
        "status": "completed",
        "source_code": v.SOURCE,
        "language": "python",
        "review_result": "## Summary\nThe average function computes a mean of values.\n\n"
        "## Findings\nEmpty values causes division by zero.\n\n"
        "## Suggestions\nGuard average against empty values before division.",
    }
    v.completed_review(review, identity)
    with pytest.raises(d.DemoError, match="identity"):
        v.completed_review(review | {"model_revision": v.REVISION}, identity)
