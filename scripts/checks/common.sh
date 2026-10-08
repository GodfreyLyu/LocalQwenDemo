#!/usr/bin/env bash
# Sourced only by check entry points, never deployment commands.
set -euo pipefail
CHECK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$CHECK_ROOT"
REVIEW_PYTHON="${REVIEW_PYTHON:-$CHECK_ROOT/backend/.venv/bin/python}"
