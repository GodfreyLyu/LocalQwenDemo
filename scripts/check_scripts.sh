#!/usr/bin/env bash
# Deterministic local regressions; no model download, deployment or real cluster operations.
set -euo pipefail
cd "$(dirname "$0")/.."
for script in scripts/*.sh; do
  bash -n "$script"
done
scripts/check_minikube_demo.sh
backend/.venv/bin/python -m pytest scripts/tests/test_model_evaluation.py scripts/tests/test_local_users_initializer.py -q
backend/.venv/bin/python scripts/validate_manifests.py
