#!/usr/bin/env bash
# CI-only schema validation. Tool download is versioned and checksum-verified.
set -euo pipefail
repo_root="${1:-.}"
tool_dir="${RUNNER_TEMP:?RUNNER_TEMP must be set}/review-kubeconform"
mkdir -p "$tool_dir"
gh release download v0.7.0 --repo yannh/kubeconform \
  --pattern kubeconform-linux-amd64.tar.gz --pattern CHECKSUMS --dir "$tool_dir" --clobber
(cd "$tool_dir" && sha256sum --check --ignore-missing CHECKSUMS && tar -xzf kubeconform-linux-amd64.tar.gz)
args=()
if [ -f "$repo_root/release-values.yaml" ]; then args+=(-f "$repo_root/release-values.yaml"); fi
for mode in ollama transformers; do
  helm template local-review "$repo_root/deploy/helm/local-review" "${args[@]}" \
    --set "model.backend=$mode" --set networkPolicy.ollamaHostCidr=192.168.49.1/32 \
    | "$tool_dir/kubeconform" -strict -summary -kubernetes-version 1.35.0
done
