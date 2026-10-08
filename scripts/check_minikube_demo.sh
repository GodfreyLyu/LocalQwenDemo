#!/usr/bin/env bash
# Compatibility entry; checks are organized under scripts/checks.
set -euo pipefail
exec bash "$(dirname "$0")/checks/deployment.sh" "$@"
