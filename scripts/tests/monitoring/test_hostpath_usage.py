"""Exercise the actual textfile collector and dashboard fallback semantics."""

import json
import os
import shutil
import subprocess

import pytest
import yaml

from scripts.tests.support.paths import ROOT

pytestmark = pytest.mark.contract
PLATFORM = ROOT / "infra/local-platform/terraform/platform"


def test_collector_measures_allocated_bytes_and_removes_failed_or_deleted_samples(tmp_path):
    values = yaml.safe_load((PLATFORM / "values/monitoring.yaml").read_text())
    exporter = values["prometheus-node-exporter"]
    collector = exporter["sidecars"][0]
    assert all(mount["readOnly"] for mount in exporter["sidecarHostVolumeMounts"])
    assert collector["securityContext"]["capabilities"] == {
        "drop": ["ALL"],
        "add": ["DAC_READ_SEARCH"],
    }
    root = tmp_path / "pvcs"
    claim = root / "local-review-demo/review-history"
    claim.mkdir(parents=True)
    (claim / "database").write_bytes(b"data" * 4096)
    (claim.parent / "symlink-claim").symlink_to(claim, target_is_directory=True)
    output = tmp_path / "metrics"
    output.mkdir()
    env = dict(os.environ, PVC_ROOT=str(root), TEXTFILE_DIRECTORY=str(output), COLLECT_ONCE="1")

    def collect():
        subprocess.run([*collector["command"], collector["args"][0]], env=env, check=True)
        return (output / "hostpath-pvcs.prom").read_text()

    text = collect()
    allocated = int(subprocess.check_output(["du", "-sk", str(claim)], text=True).split()[0]) * 1024
    assert f'persistentvolumeclaim="review-history"}} {allocated}\n' in text
    assert "local_hostpath_pvc_last_success_timestamp_seconds{" in text
    assert "symlink-claim" not in text
    # Simulate du failing after producing partial output; it must not publish that sample.
    binary = tmp_path / "bin"
    binary.mkdir()
    fake_du = binary / "du"
    fake_du.write_text('#!/bin/sh\nprintf "1 partial\\n"\nexit 1\n')
    fake_du.chmod(0o755)
    env["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
    assert "persistentvolumeclaim=" not in collect()
    env["PATH"] = os.environ["PATH"]
    shutil.rmtree(claim)
    assert "persistentvolumeclaim=" not in collect()


def test_usage_query_prefers_kubelet_and_rejects_stale_or_deleted_fallbacks(tmp_path):
    promtool = shutil.which(os.environ.get("PROMTOOL", "promtool"))
    if not promtool:
        pytest.skip("Set PROMTOOL for PVC fallback scenarios")
    board = json.loads(
        (
            ROOT / "deploy/helm/local-review/files/dashboards/review-inference-resources.json"
        ).read_text()
    )
    panel = next(p for p in board["panels"] if p["title"] == "Application persistent volume usage")
    query = panel["targets"][0]["expr"].replace("$namespace", "local-review-demo")
    pvc = 'persistentvolumeclaim="review-history"'
    kube = f'namespace="local-review-demo",{pvc}'
    # Node-exporter already carries namespace=monitoring from discovery.
    local = f'namespace="monitoring",claim_namespace="local-review-demo",{pvc}'
    measured = {"series": f"local_hostpath_pvc_used_bytes{{{local}}}", "values": "2048x5"}
    fresh = {
        "series": f"local_hostpath_pvc_last_success_timestamp_seconds{{{local}}}",
        "values": "0+60x5",
    }
    info = {"series": f"kube_persistentvolumeclaim_info{{{kube}}}", "values": "1x5"}
    scenarios = []
    for name, series, expected in (
        (
            "fresh directory fallback",
            [measured, fresh, info],
            [{"labels": f'{{claim_namespace="local-review-demo",{kube}}}', "value": 2048}],
        ),
        (
            "kubelet takes precedence",
            [
                measured,
                fresh,
                info,
                {"series": f"kubelet_volume_stats_used_bytes{{{kube}}}", "values": "4096x5"},
            ],
            [{"labels": "{" + kube + "}", "value": 4096}],
        ),
        (
            "stale fallback omitted",
            [measured, info, {"series": fresh["series"], "values": "0x5"}],
            [],
        ),
        ("deleted PVC omitted", [measured, fresh], []),
        ("unknown usage omitted", [info], []),
    ):
        scenarios.append(
            {
                "name": name,
                "interval": "1m",
                "input_series": series,
                "promql_expr_test": [{"expr": query, "eval_time": "5m", "exp_samples": expected}],
            }
        )
    path = tmp_path / "usage.test.yaml"
    path.write_text(
        yaml.safe_dump({"rule_files": [], "evaluation_interval": "1m", "tests": scenarios})
    )
    subprocess.run([promtool, "test", "rules", str(path)], check=True)
