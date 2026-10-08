"""Keep public commands usable and shared tooling independent of CLI state."""

import ast
import subprocess
import sys
from types import ModuleType

import pytest
from deployment.legacy.context import api, using_context
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
                    # The explicit `legacy` subcommand is the sole dispatch boundary.
                    if path.name == "cli.py" and name == "deployment.legacy.runtime":
                        continue
                    assert not name.startswith(("deployment.legacy", "minikube_")), path
                    if directory == "deployment/common":
                        assert not name.startswith("deployment.helm"), path
                    if directory == "validation":
                        assert name not in {"validation.helm", "validate_helm"}, path


@pytest.mark.unit
@pytest.mark.contract
def test_legacy_context_is_explicit_and_restored_after_failure():
    with pytest.raises(RuntimeError, match="explicit operation context"):
        api()
    outer, inner = ModuleType("outer"), ModuleType("inner")
    with using_context(outer):
        with pytest.raises(ValueError), using_context(inner):
            assert api() is inner
            raise ValueError("operation failed")
        assert api() is outer
    with pytest.raises(RuntimeError, match="explicit operation context"):
        api()


@pytest.mark.integration
@pytest.mark.contract
@pytest.mark.parametrize(
    "command",
    [
        ["minikube_helm.py"],
        ["minikube_helm.py", "legacy"],
        ["minikube_demo.py"],
        ["helm_deploy.py"],
        ["local_demo.py"],
        ["evaluate_model.py"],
        ["validate_helm.py"],
        ["release/publish.py"],
        ["release/snapshot.py"],
        ["release/migrate.py"],
        ["release/bootstrap.py"],
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
