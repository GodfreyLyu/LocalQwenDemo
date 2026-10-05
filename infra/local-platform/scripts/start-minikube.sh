#!/usr/bin/env bash
# CLI-only cluster lifecycle. Terraform never executes this script.
set -euo pipefail

profile=minikube
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      [ "$#" -ge 2 ] || { echo '--profile requires a value' >&2; exit 2; }
      profile="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--profile minikube]"
      echo 'New clusters: MINIKUBE_CPUS=2 MINIKUBE_MEMORY=4096 MINIKUBE_KUBERNETES_VERSION=v1.37.0'
      echo 'KUBE_CONFIG_PATH selects one kubeconfig file (default: ~/.kube/config).'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$profile" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || { echo 'Invalid profile' >&2; exit 2; }
for tool in minikube python3; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || {
  echo 'The krunkit driver requires Apple silicon macOS.' >&2; exit 1;
}
export KUBECONFIG="${KUBE_CONFIG_PATH:-$HOME/.kube/config}"

# Inspect before starting: never convert, delete or resize an existing profile.
# Minikube can return nonzero with a JSON report when no profiles exist.
profiles_json="$(minikube profile list -o json)" || {
  [ -n "$profiles_json" ] || exit 1
}
profile_state="$(printf '%s' "$profiles_json" | python3 -c '
import json, sys
report = json.load(sys.stdin)
name = sys.argv[1]
if any(p.get("Name") == name for p in report.get("invalid", [])):
    sys.exit("Existing profile is invalid; inspect it manually before starting.")
matches = [p for p in report.get("valid", []) if p.get("Name") == name]
if not matches:
    print("new")
else:
    p = matches[0]
    cfg = p["Config"]
    if cfg.get("Driver") != "krunkit" or cfg.get("KubernetesConfig", {}).get("ContainerRuntime") != "containerd":
        sys.exit("Existing profile must use krunkit/containerd; refusing to change it.")
    if len(cfg.get("Nodes", [])) != 1:
        sys.exit("This local platform expects a single-node profile.")
    status = p.get("Status")
    if status == "OK":
        print("running")
    elif status in {"Stopped", "Paused"}:
        print("stopped")
    else:
        sys.exit("Could not establish a healthy or stopped profile state; inspect minikube status first.")
' "$profile")"

case "$profile_state" in
  running)
    echo "Profile $profile is already running; no start or reconfiguration needed." ;;
  stopped)
    minikube start --profile "$profile" --keep-context ;;
  new)
    minikube start --profile "$profile" --driver krunkit --container-runtime containerd \
      --kubernetes-version "${MINIKUBE_KUBERNETES_VERSION:-v1.37.0}" \
      --cpus "${MINIKUBE_CPUS:-2}" --memory "${MINIKUBE_MEMORY:-4096}" --keep-context ;;
esac
minikube --profile "$profile" ssh -- 'test -c /dev/dri/renderD128 && ls -l /dev/dri'
echo 'Node render device exists. Next: terraform init/plan/apply, then verify-gpu.sh.'
