"""Offline regression tests; these are never evidence of real-model acceptance."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import deployment.legacy.runtime as demo  # noqa: E402
import deployment.legacy.target as target  # noqa: E402
import pytest

from scripts.tests.support.deployment import profile
from scripts.tests.support.paths import ROOT


@pytest.mark.unit
@pytest.mark.security
def test_every_kubectl_operation_has_private_context_and_namespace(monkeypatch):
    called = Mock(return_value=SimpleNamespace(stdout=""))
    monkeypatch.setattr(demo, "run", called)
    demo.k("get", "pods")
    command = called.call_args.args[0]
    assert command[:7] == [
        "kubectl",
        "--kubeconfig",
        str(demo.KUBECONFIG_FILE),
        "--context",
        demo.PROFILE,
        "--namespace",
        demo.NAMESPACE,
    ]
    assert demo.clean_env()["MINIKUBE_HOME"].endswith("/.minikube")


@pytest.mark.unit
def test_host_aws_profiles_and_hub_tokens_are_not_inherited(monkeypatch):
    for key in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "HF_TOKEN", "MINIKUBE_DRIVER"):
        monkeypatch.setenv(key, "do-not-use")
        assert key not in demo.clean_env()
    assert demo.clean_env()["AWS_CONFIG_FILE"] == "/dev/null"
    assert demo.clean_env()["AWS_EC2_METADATA_DISABLED"] == "true"


@pytest.mark.unit
@pytest.mark.security
def test_initializer_still_refuses_cluster_or_real_aws_endpoint(monkeypatch):
    path = ROOT / "scripts/dev/initialize_users.py"
    for endpoint in (
        "http://review-dynamodb:8000",
        "https://dynamodb.ap-northeast-1.amazonaws.com",
    ):
        monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", endpoint)
        spec = importlib.util.spec_from_file_location("loopback_initializer", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with pytest.raises(ValueError, match="DynamoDB Local endpoint"):
            module.initialize()


@pytest.mark.unit
def test_no_running_cluster_requires_manual_start():
    with pytest.raises(
        demo.DemoError, match="Please start minikube manually before running the deployment command"
    ):
        target.select_profile([profile("stopped", "Stopped")])


@pytest.mark.unit
def test_single_running_profile_is_selected():
    value = target.select_profile([profile("stopped", "Stopped"), profile("chosen")])
    assert value["Name"] == "chosen"


@pytest.mark.unit
def test_multiple_running_profiles_require_explicit_choice():
    profiles = [profile("one"), profile("two")]
    with pytest.raises(demo.DemoError, match="--profile NAME"):
        target.select_profile(profiles)
    assert target.select_profile(profiles, "two")["Name"] == "two"


@pytest.mark.unit
@pytest.mark.parametrize(
    "requested, rows, message",
    [
        ("missing", [], "does not exist"),
        ("stopped", [profile("stopped", "Stopped")], "is not running"),
    ],
)
def test_explicit_missing_or_stopped_profile_fails(requested, rows, message):
    with pytest.raises(demo.DemoError, match=message):
        target.select_profile(rows, requested)


@pytest.mark.integration
def test_explicit_api_unavailable_fails_before_docker_inspection(monkeypatch, tmp_path):
    item = profile("selected") | {"APIServer": "Stopped"}
    monkeypatch.setattr(target, "discover_profiles", lambda _: [item])
    monkeypatch.setattr(demo, "require_local_docker", lambda: None)
    # Global changes are scoped to the fixture, as they are to one process in the CLI.
    for key in ("PROFILE", "MINIKUBE_HOME", "KUBECONFIG_FILE"):
        monkeypatch.setattr(demo, key, getattr(demo, key))
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    with pytest.raises(demo.DemoError, match="API/kubelet is unavailable"):
        with target.connected_target(SimpleNamespace(minikube_home=tmp_path, profile="selected")):
            pytest.fail("Must not connect")
    calls.assert_not_called()


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    "verb", ["create", "start", "stop", "delete", "restart", "pause", "unpause", "config", "addons"]
)
def test_minikube_mutation_allowlist_refuses_cluster_lifecycle(monkeypatch, verb):
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    with pytest.raises(demo.DemoError, match="Only minikube image load"):
        demo.mk(verb)
    calls.assert_not_called()


@pytest.mark.unit
def test_stop_is_advice_only(monkeypatch, capsys):
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    monkeypatch.setattr(target, "connected_target", calls)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "stop"])
    demo.main()
    assert "performs no operations" in capsys.readouterr().out
    calls.assert_not_called()


@pytest.mark.unit
def test_help_is_english_and_does_not_access_a_cluster(monkeypatch, capsys):
    calls = Mock()
    monkeypatch.setattr(demo, "run", calls)
    monkeypatch.setattr(target, "connected_target", calls)
    monkeypatch.setattr(sys, "argv", ["minikube_demo", "--help"])
    with pytest.raises(SystemExit) as result:
        demo.main()
    assert result.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "You manage the cluster; this script deploys the application." in output
    assert "existing running profile" in output
    assert "does not pass persistence acceptance" in output
    assert "doctor exits nonzero for failed or incomplete diagnostics" in output
    assert "up warns and attempts deployment" in output
    calls.assert_not_called()


@pytest.mark.integration
@pytest.mark.contract
def test_target_states_separate_profiles_homes_and_cluster_identity(tmp_path):
    one = target.target_path(tmp_path, Path("/one/.minikube"), "p", "uid-one")
    assert (
        len(
            {
                one,
                target.target_path(tmp_path, Path("/two/.minikube"), "p", "uid-one"),
                target.target_path(tmp_path, Path("/one/.minikube"), "other", "uid-one"),
                target.target_path(tmp_path, Path("/one/.minikube"), "p", "uid-two"),
            }
        )
        == 4
    )
    assert one != tmp_path / "owner.json"


@pytest.mark.integration
@pytest.mark.contract
def test_state_identity_mismatch_refuses_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "STATE", tmp_path)
    monkeypatch.setattr(
        demo,
        "TARGET",
        {"root": "here", "profile": "new", "minikube_home": "/home", "cluster_uid": "new"},
    )
    (tmp_path / "owner.json").write_text(json.dumps(demo.TARGET | {"cluster_uid": "old"}))
    with pytest.raises(demo.DemoError, match="another minikube home/profile/cluster"):
        demo.state()
    assert json.loads((tmp_path / "owner.json").read_text())["cluster_uid"] == "old"


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    ["doctor", "up", "verify", "status", "logs", "port-forward", "import-state", "undeploy"],
)
def test_all_cluster_commands_share_no_running_selection_gate(monkeypatch, tmp_path, command):
    monkeypatch.setattr(sys, "argv", ["minikube_demo", command])
    monkeypatch.setattr(demo, "require_local_docker", lambda: None)
    monkeypatch.setattr(target, "discover_profiles", lambda _: [])
    monkeypatch.setattr(demo, "KUBECONFIG_FILE", tmp_path / "unused")
    monkeypatch.setattr(demo, "MINIKUBE_HOME", tmp_path / ".minikube")
    run = Mock()
    monkeypatch.setattr(demo, "run", run)
    with pytest.raises(
        demo.DemoError, match="Please start minikube manually before running the deployment command"
    ):
        demo.main()
    run.assert_not_called()
    assert not (tmp_path / "unused").exists()
