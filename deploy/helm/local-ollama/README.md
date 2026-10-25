# Generic LLM inference workload (existing Ollama release)

This Chart deploys the combined generic LLM service and supervised Ollama child on
an existing ARM64 krunkit Minikube cluster. The independent image is built from
[`inference-service/Dockerfile`](../../../inference-service/Dockerfile), with Ollama
`0.24.0-4.fc45` and the downstream Mesa Venus alignment fix
`25.3.6-102.fc44`. The Fedora 45 / Fedora 44 preserved package combination was previously tested on an
Apple M2 Pro; it is a local experimental platform, not a portable NVIDIA image.
See the [driver issue](https://github.com/libkrun/krunkit/issues/114).

## Installation

Run from the repository root. The existing device plugin must advertise
`devic.es/dri` with at least one free slot, and the node must have
`/dev/dri/renderD128`. The [platform startup script](../../../infra/local-platform/README.md)
defaults to 4 CPUs and 8 GiB RAM for a new cluster. Check available capacity for
Ollama, the application and any platform services before installing them.
Terraform manages the device plugin; this Helm release owns the inference workload and model PVC.
Use the commands below for Helm-managed installations. For an Argo CD deployment,
follow the [GitOps guide](../../../docs/guides/gitops.md).

### Install a published release

Run from an approved `deployment-release` checkout. The snapshot includes a published
image digest in `deploy/helm/local-ollama/values-release.yaml`; no image build is needed.
Create the namespace if it does not already exist:

```bash
kubectl --context minikube create namespace local-inference
```

For a private GHCR image, create a pull Secret in `local-inference`. Secrets in the
application namespace cannot be used here. The file below must contain registry
credentials in its `auths` field, not just a reference to a local credential helper:

```bash
kubectl --context minikube -n local-inference create secret generic ghcr-pull --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=/secure/path/ghcr-config.json
```

Save your environment values outside the checkout, for example as
`/path/to/ollama-values.yaml`. For the private image, include:

```yaml
imagePullSecrets:
  - name: ghcr-pull
```

For a public image, skip the Secret and use `imagePullSecrets: []` in that file.
Install using the published image values followed by your environment values:

```bash
helm upgrade --install review-ollama deploy/helm/local-ollama --kube-context minikube --namespace local-inference -f deploy/helm/local-ollama/values-release.yaml -f /path/to/ollama-values.yaml --wait --timeout 35m
```

Pass both files on each upgrade, then run the [GPU check](#readiness-and-gpu-verification).
Replace `minikube` with your cluster context if it differs.

### Build from source

Run these commands from the main source checkout. Release snapshots do not contain
the Docker build directory:

```bash
docker build --platform linux/arm64 -f inference-service/Dockerfile -t review-inference:local .
```

```bash
minikube --profile minikube image load review-inference:local --daemon=true
```

```bash
helm lint deploy/helm/local-ollama
```

```bash
helm upgrade --install review-ollama deploy/helm/local-ollama --kube-context minikube --namespace local-inference --create-namespace --wait --timeout 35m
```

Then run the [GPU check](#readiness-and-gpu-verification).

### Model storage

The inference image starts and supervises Ollama itself. With `model.pullIfMissing=true`,
the first start downloads the pinned model into the retained 4 GiB PVC; subsequent
starts reuse it. A different installed digest fails instead of replacing model data.
Thrift opens only after a generic warmup, capability/context checks and full GPU
residency verification. Application prompts and review validation remain in the backend.

The image pins the base digest and the Ollama/Mesa package versions; other OS
dependencies still resolve from Fedora repositories. The complete installed RPM
list is embedded at `/etc/ollama-image-packages.txt`. For registry-based delivery,
publish a tested image and set `image.repository` plus `image.digest`; do not
reuse a mutable tag for changed runtime code. The repository's release workflow builds
and publishes this image alongside the application images; the Chart itself does not
publish images.

For offline installation, prepare the existing PVC with the complete pinned model
store (all blobs before manifests), preserve ownership/readability for UID 10001 and
set `model.pullIfMissing=false`. No model conversion or path migration is required.
A missing offline model fails startup without downloading. Use
`persistence.existingClaim` only when explicitly attaching a previously retained claim.

## Readiness and GPU verification

After a Helm installation or upgrade, run the GPU inference check:

```bash
helm test review-ollama --kube-context minikube --namespace local-inference --logs --timeout 5m
```

The runtime runs as UID/GID 10001 with a read-only root filesystem, dropped
capabilities, and only PVC and `/tmp` writes. A small root init container changes
ownership of the PVC root; no privileged workload or hostPath is used. Imported
files must also be readable by UID 10001. Set `volumePermissions.enabled=false`
when the storage system already supplies the correct permissions.

- Startup validates the pinned model and performs real generic warmup.
- Readiness reports loaded adapter and child health, and is false during recovery.
- Liveness detects unexpected child exit; cancellation deliberately reaps/restarts it.
- `helm test` and Argo PostSync use generated Thrift calls and verify actual GPU
  generation without claiming a GPU device for the verification client.

`100% GPU` describes layer placement, not zero CPU use. CPU work and host memory
remain necessary. The readiness check is not an accuracy evaluation or load test.

```bash
kubectl --context minikube -n local-inference get deployment,pods,svc,pvc
```

```bash
kubectl --context minikube -n local-inference logs deployment/review-ollama -c inference
```

```bash
kubectl --context minikube -n local-inference exec deployment/review-ollama -c inference -- ollama ps
```

```bash
kubectl --context minikube -n local-inference port-forward service/review-ollama 9091:9091 --address 127.0.0.1
```

The Service exposes Thrift 9091 and health 11435. Ollama HTTP stays on loopback
inside the inference container. There is no RPC authentication/TLS; use a trusted
cluster and an enforcing CNI for the provided NetworkPolicy. Configure the actual
backend namespace/release in `networkPolicy.backendNamespace/backendRelease`.

## Connect the application

The backend connects to `review-ollama.local-inference.svc.cluster.local:9091`.
It needs its own image plus `inference.host/port`; the inference image is packaged
independently. The earlier separate `review-inference` Deployment is removed.
Default backend identity assertions match the service's pinned model. The application
chart owns prompt/section budgets; `local-ollama` owns context, resource/execution
limits and model lifecycle. Keep both sets of release ownership and PVCs unchanged.
For nondefault namespaces/releases, update both charts' policy selectors consistently.
See [the complete setup and learning guide](../../../docs/guides/apache-thrift.md).

## Upgrade, rollback and storage

For this protocol migration, follow [coordinated migration and rollback](../../../docs/operations/inference-migration.md). Restore both charts and images together; do not change active releases as part of source verification.

The deployment uses one replica and the `Recreate` strategy. It releases the GPU slot
before scheduling the replacement Pod, so upgrades cause downtime. High availability is
not supported.

```bash
helm history review-ollama --kube-context minikube -n local-inference
```

```bash
helm rollback review-ollama REVISION --kube-context minikube -n local-inference --wait --timeout 35m
```

```bash
helm uninstall review-ollama --kube-context minikube -n local-inference
```

The model PVC `review-ollama-models` is retained on Helm uninstall. To reinstall
using it, set `persistence.existingClaim=review-ollama-models`. Never delete the
namespace when you intend to preserve its PVC. Rollback restores workload
configuration, not a snapshot of model storage. Keep old model blobs when testing
model upgrades, and size the PVC for both versions.

For deliberate full removal, first uninstall the release, then delete its PVC:

```bash
kubectl --context minikube -n local-inference delete pvc review-ollama-models
```

With Minikube's `standard` StorageClass, this also deletes the stored model data.

## GitHub Actions and Argo CD

When its build inputs change, the main release workflow builds this image for
linux/arm64 after PR checks and merge, publishes a GHCR digest, and includes this
Chart in the deployment snapshot. Argo CD uses `values-release.yaml` plus
`values-argocd.yaml`. When bootstrap is enabled, its PostSync Job verifies GPU inference
for the pinned model through
the Service. The Job is disabled for normal Helm installs, where `helm test` remains
available. See the
[GitOps guide](../../../docs/guides/gitops.md) for first installation and migration.
