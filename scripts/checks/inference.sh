#!/usr/bin/env bash
# Independent inference package and Python/Java wire gate; no real model is loaded.
set -euo pipefail
source "$(dirname "$0")/common.sh"
inference_python="${INFERENCE_PYTHON:-inference-service/.venv/bin/python}"
"$inference_python" -m ruff check --config inference-service/pyproject.toml inference-service/src inference-service/tests
"$inference_python" -m ruff format --check --config inference-service/pyproject.toml inference-service/src inference-service/tests
"$REVIEW_PYTHON" scripts/generate_thrift.py --check
"$REVIEW_PYTHON" scripts/java_thrift.py --compile-only
RUN_JAVA_INTEROP=1 "$inference_python" -m pytest -c inference-service/pytest.ini inference-service/tests
