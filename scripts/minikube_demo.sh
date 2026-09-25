#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${REVIEW_PYTHON:-$ROOT/backend/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo 'Missing Python environment. Follow docs/guides/local-development.md, or set REVIEW_PYTHON.' >&2
  exit 1
fi
exec "$PYTHON" "$ROOT/scripts/minikube_demo.py" "$@"
