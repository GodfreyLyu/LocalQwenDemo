#!/usr/bin/env bash
# Complete offline gate; browser/schema/real-environment checks have separate entries.
set -euo pipefail
source "$(dirname "$0")/common.sh"
"$REVIEW_PYTHON" -m ruff check --config backend/pyproject.toml backend/app backend/tests deploy/images/ollama-vulkan/runtime.py
"$REVIEW_PYTHON" -m ruff format --check --config backend/pyproject.toml backend/app backend/tests deploy/images/ollama-vulkan/runtime.py
"$REVIEW_PYTHON" -m pytest backend/tests -m 'not real_model and not requires_cluster'
(cd frontend && npm run lint && npm test && npm run build)
bash scripts/checks/scripts.sh
