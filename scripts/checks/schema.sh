#!/usr/bin/env bash
# CI-only schema validation. Tool download is versioned and checksum-verified.
set -euo pipefail
repo_root="${1:-.}"
schema_dir="$(cd "$(dirname "$0")/../validation/schemas" && pwd)"
tool_dir="${RUNNER_TEMP:?RUNNER_TEMP must be set}/review-kubeconform"
mkdir -p "$tool_dir"
gh release download v0.7.0 --repo yannh/kubeconform \
  --pattern kubeconform-linux-amd64.tar.gz --pattern CHECKSUMS --dir "$tool_dir" --clobber
(cd "$tool_dir" && sha256sum --check --ignore-missing CHECKSUMS && tar -xzf kubeconform-linux-amd64.tar.gz)
args=()
if [ -f "$repo_root/release-values.yaml" ]; then args+=(-f "$repo_root/release-values.yaml"); fi
# Archived rollback snapshots retain their original hand-written values files.
# Accept the old Helm environment alone or the complete former Argo composition;
# reject partial overlay sets rather than silently checking a different deployment.
chart="$repo_root/deploy/helm/local-review"
if [ -f "$chart/values-minikube.yaml" ]; then
  args+=(-f "$chart/values-minikube.yaml")
  if [ -f "$chart/values-minikube-ollama.yaml" ] && [ -f "$chart/values-monitoring.yaml" ]; then
    args+=(-f "$chart/values-minikube-ollama.yaml" -f "$chart/values-monitoring.yaml")
  elif [ -f "$chart/values-minikube-ollama.yaml" ] || [ -f "$chart/values-monitoring.yaml" ]; then
    echo "Incomplete historical application values layout" >&2
    exit 1
  fi
elif [ -f "$chart/values-minikube-ollama.yaml" ] || [ -f "$chart/values-monitoring.yaml" ]; then
  echo "Incomplete historical application values layout" >&2
  exit 1
fi
helm template local-review "$repo_root/deploy/helm/local-review" "${args[@]}" \
  | "$tool_dir/kubeconform" -strict -summary -kubernetes-version 1.35.0 \
    -schema-location default \
    -schema-location "$schema_dir/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
helm template local-review "$repo_root/deploy/helm/local-review" "${args[@]}" \
  --set monitoring.enabled=false \
  | "$tool_dir/kubeconform" -strict -summary -kubernetes-version 1.35.0
helm template local-review "$repo_root/deploy/helm/local-review" "${args[@]}" \
  --set networkPolicy.enabled=true \
  | "$tool_dir/kubeconform" -strict -summary -kubernetes-version 1.35.0 \
    -schema-location default \
    -schema-location "$schema_dir/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
if [ -d "$repo_root/deploy/helm/local-ollama" ]; then
  ollama_args=()
  if [ -f "$repo_root/deploy/helm/local-ollama/values-release.yaml" ]; then
    ollama_args+=(-f "$repo_root/deploy/helm/local-ollama/values-release.yaml")
  fi
  # Standard Helm keeps PostSync verification disabled and retains its test Pod.
  helm template review-ollama "$repo_root/deploy/helm/local-ollama" \
    --namespace local-inference "${ollama_args[@]}" \
    | "$tool_dir/kubeconform" -strict -summary -kubernetes-version 1.35.0
  # Preserve archived snapshots' overlay; current Argo Applications use this parameter.
  ollama_argo_args=("${ollama_args[@]}")
  if [ -f "$repo_root/deploy/helm/local-ollama/values-argocd.yaml" ]; then
    ollama_argo_args+=(-f "$repo_root/deploy/helm/local-ollama/values-argocd.yaml")
  else
    ollama_argo_args+=(--set verification.argoPostSync=true)
  fi
  helm template review-ollama "$repo_root/deploy/helm/local-ollama" \
    --namespace local-inference --skip-tests "${ollama_argo_args[@]}" \
    | "$tool_dir/kubeconform" -strict -summary -kubernetes-version 1.35.0
fi
