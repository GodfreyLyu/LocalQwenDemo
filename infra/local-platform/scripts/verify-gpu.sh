#!/usr/bin/env bash
# Read-only checks. Does not install the plugin or create the smoke-test Pod.
set -euo pipefail

profile=minikube
context=''
namespace=gpu-system
expected_slots=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile|--context|--namespace|--expected-slots)
      [ "$#" -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }
      case "$1" in
        --profile) profile="$2" ;;
        --context) context="$2" ;;
        --namespace) namespace="$2" ;;
        --expected-slots) expected_slots="$2" ;;
      esac
      shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--profile minikube] [--context minikube] [--namespace gpu-system] [--expected-slots 1]"
      echo 'KUBE_CONFIG_PATH selects one kubeconfig file (default: ~/.kube/config).'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$expected_slots" =~ ^[1-9][0-9]*$ ]] || { echo 'Expected slots must be a positive integer' >&2; exit 2; }
context="${context:-$profile}"
for tool in minikube kubectl python3; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done
kube=(kubectl --kubeconfig "${KUBE_CONFIG_PATH:-$HOME/.kube/config}" --context "$context" --request-timeout=15s)

node_ip="$(minikube --profile "$profile" ip)"
"${kube[@]}" get nodes -o json | python3 -c '
import json, sys
nodes = json.load(sys.stdin)["items"]
if len(nodes) != 1:
    sys.exit("Expected a single-node cluster.")
node = nodes[0]
addresses = node["status"].get("addresses", [])
if not any(a["type"] == "InternalIP" and a["address"] == sys.argv[1] for a in addresses):
    sys.exit("Kubeconfig context does not match the selected Minikube profile IP.")
if not any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"].get("conditions", [])):
    sys.exit("Node is not Ready.")
print("PASS: selected cluster matches the profile and its node is Ready.")
' "$node_ip"
minikube --profile "$profile" ssh -- 'test -c /dev/dri/renderD128 && ls -l /dev/dri'

"${kube[@]}" -n "$namespace" rollout status daemonset/generic-device-plugin --timeout=120s
"${kube[@]}" -n "$namespace" get pods -l app.kubernetes.io/name=generic-device-plugin -o wide

# Rollout health alone does not prove that kubelet has registered the resource.
registered=false
for ((attempt=0; attempt<30; attempt++)); do
  if "${kube[@]}" get nodes -o json | python3 -c '
import json, sys
nodes = json.load(sys.stdin)["items"]
expected = int(sys.argv[1])
ok = len(nodes) == 1
for node in nodes:
    status = node["status"]
    capacity = int(status.get("capacity", {}).get("devic.es/dri", "0"))
    allocatable = int(status.get("allocatable", {}).get("devic.es/dri", "0"))
    name = node["metadata"]["name"]
    print(f"{name}: devic.es/dri capacity={capacity}, allocatable={allocatable}, expected={expected}")
    ok = ok and capacity == expected and allocatable == expected
sys.exit(0 if ok else 1)
' "$expected_slots"; then
    registered=true
    break
  fi
  sleep 2
done
[ "$registered" = true ] || {
  echo 'GPU registration mismatch. Inspect plugin logs and node description.' >&2
  exit 1
}
echo 'PASS: node device, plugin rollout and devic.es/dri registration.'
echo 'Allocatable is total schedulable capacity, not the number of currently free slots.'
echo 'Run kubernetes/tests/gpu-smoke-test.yaml separately to check Pod device injection.'
