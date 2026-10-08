#!/usr/bin/env bash
# Focused deployment regressions, including all shared helpers and offline sockets.
set -euo pipefail
source "$(dirname "$0")/common.sh"
bash -n scripts/minikube_demo.sh scripts/check_minikube_demo.sh scripts/checks/deployment.sh scripts/checks/common.sh
"$REVIEW_PYTHON" -m ruff check --config backend/pyproject.toml scripts/deployment scripts/tests/deployment scripts/tests/support/deployment.py scripts/tooling_paths.py
"$REVIEW_PYTHON" -m ruff format --check --config backend/pyproject.toml scripts/deployment scripts/tests/deployment scripts/tests/support/deployment.py scripts/tooling_paths.py
"$REVIEW_PYTHON" -m pytest scripts/tests/deployment -m 'not real_model and not requires_cluster'
