#!/usr/bin/env bash
# Network-dependent CI check; the offline gate never downloads validation tools.
set -euo pipefail
source_root="$(cd "$(dirname "$0")/.." && pwd)"
repo_root="${1:-$source_root}"
tool_dir="${RUNNER_TEMP:?RUNNER_TEMP must be set}/review-promtool"
mkdir -p "$tool_dir"
gh release download v3.15.0 --repo prometheus/prometheus \
  --pattern prometheus-3.15.0.linux-amd64.tar.gz --pattern sha256sums.txt \
  --dir "$tool_dir" --clobber
(cd "$tool_dir" && sha256sum --check --ignore-missing sha256sums.txt && \
  tar -xzf prometheus-3.15.0.linux-amd64.tar.gz prometheus-3.15.0.linux-amd64/promtool)
"${REVIEW_PYTHON:-python3}" "$source_root/scripts/validate_monitoring.py" \
  --root "$repo_root" --promtool "$tool_dir/prometheus-3.15.0.linux-amd64/promtool"
