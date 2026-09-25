#!/usr/bin/env bash
# Local quality gate; requires installed development dependencies and kubectl. No deployment or model download.
set -euo pipefail
cd "$(dirname "$0")/.."
backend/.venv/bin/ruff check --config backend/pyproject.toml backend/app backend/tests scripts
backend/.venv/bin/ruff format --check --config backend/pyproject.toml backend/app backend/tests scripts
(cd backend && .venv/bin/pytest)
(cd frontend && npm run lint && npm test && npm run build)
scripts/check_scripts.sh
