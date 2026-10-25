"""Keep public commands usable and shared tooling independent of CLI state."""

import ast
import subprocess
import sys

import pytest
from tooling_paths import ROOT


@pytest.mark.unit
@pytest.mark.contract
def test_shared_modules_do_not_import_command_or_legacy_layers():
    for directory in ("deployment/common", "deployment/helm", "validation"):
        for path in (ROOT / "scripts" / directory).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                for name in imports:
                    assert not name.startswith(("deployment.legacy", "minikube_")), path
                    if directory == "deployment/common":
                        assert not name.startswith("deployment.helm"), path
                    if directory == "validation":
                        assert name not in {"validation.helm", "validate_helm"}, path


@pytest.mark.integration
@pytest.mark.contract
@pytest.mark.parametrize(
    "command",
    [
        ["minikube_helm.py"],
        ["minikube_endpoints.py"],
        ["local_demo.py"],
        ["generate_thrift.py"],
        ["java_thrift.py"],
        ["thrift_demo.py"],
        ["evaluate_model.py"],
        ["validate_helm.py"],
        ["validate_monitoring.py"],
        ["release/publish.py"],
        ["release/snapshot.py"],
    ],
    ids=lambda command: "-".join(command),
)
def test_public_help_works_outside_checkout(command, tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / command[0]), *command[1:], "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.unit
@pytest.mark.contract
def test_application_projects_share_only_generated_contracts():
    for directory, forbidden in (("backend/app", "llm_service"), ("inference-service/src", "app")):
        for path in (ROOT / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                imports = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                assert not any(
                    name == forbidden or name.startswith(forbidden + ".") for name in imports
                ), path
    service_code = "\n".join(p.read_text() for p in (ROOT / "inference-service/src").rglob("*.py"))
    assert all(
        name not in service_code
        for name in ("SECTION_SPECS", "validate_review_output", "build_prompt_messages")
    )
