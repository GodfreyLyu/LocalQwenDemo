#!/usr/bin/env bash
# Focused offline minikube gate: lint, format and tests, including real loopback subprocesses.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${REVIEW_PYTHON:-backend/.venv/bin/python}"
bash -n scripts/minikube_demo.sh scripts/check_minikube_demo.sh
"$PYTHON" -m ruff check --config backend/pyproject.toml scripts/minikube_*.py scripts/tests/test_minikube_*.py scripts/tests/conftest.py
"$PYTHON" -m ruff format --check --config backend/pyproject.toml scripts/minikube_*.py scripts/tests/test_minikube_*.py scripts/tests/conftest.py
"$PYTHON" -m pytest scripts/tests/test_minikube_*.py -q
