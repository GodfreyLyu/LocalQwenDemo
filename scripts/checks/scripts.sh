#!/usr/bin/env bash
# Every script regression and offline validator, with no live cluster/model access.
set -euo pipefail
source "$(dirname "$0")/common.sh"
while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find scripts -type f -name '*.sh' -print0)
"$REVIEW_PYTHON" -m ruff check --config backend/pyproject.toml scripts
"$REVIEW_PYTHON" -m ruff format --check --config backend/pyproject.toml scripts
"$REVIEW_PYTHON" -m pytest scripts/tests -m 'not real_model and not requires_cluster'
"$REVIEW_PYTHON" scripts/validate_helm.py
helm lint deploy/helm/local-ollama
