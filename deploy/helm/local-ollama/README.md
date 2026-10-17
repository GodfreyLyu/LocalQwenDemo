# Independent Ollama GPU service

This Chart deploys Ollama independently of the code-review application on an
existing ARM64 krunkit Minikube cluster. It uses the Vulkan runtime built from
[`deploy/images/ollama-vulkan`](../../images/ollama-vulkan), with Ollama
`0.24.0-4.fc45` and the downstream Mesa Venus alignment fix
`25.3.6-102.fc44`. The Fedora 45 / Fedora 44 package combination was tested on an
Apple M2 Pro; it is a local experimental platform, not a portable NVIDIA image.
See the [driver issue](https://github.com/libkrun/krunkit/issues/114).

## Installation

Run from the repository root. The existing device plugin must advertise
`devic.es/dri` with at least one free slot, and the node must have
`/dev/dri/renderD128`. The [platform startup script](../../../infra/local-platform/README.md)
defaults to 4 CPUs and 8 GiB RAM for a new cluster. Check available capacity for
Ollama, the application and any platform services before installing them.
Terraform manages the device plugin; this Helm release manages only Ollama.
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
docker build --platform linux/arm64 -t review-ollama:0.24.0-krunkit1 deploy/images/ollama-vulkan
```

```bash
minikube --profile minikube image load review-ollama:0.24.0-krunkit1 --daemon=true
```

```bash
helm lint deploy/helm/local-ollama
```

```bash
helm upgrade --install review-ollama deploy/helm/local-ollama --kube-context minikube --namespace local-inference --create-namespace --wait --timeout 35m
```

Then run the [GPU check](#readiness-and-gpu-verification).

### Model storage

No project deployment wrapper or host Ollama service is required. With the default
`model.bootstrap=true`, the first start downloads the configured model through Ollama
into the 4 GiB PVC. Subsequent starts reuse it. A different digest under the same
model tag fails validation instead of silently replacing the expected model.
Network access to the model registry is
needed only when downloading missing models.

The image pins the base digest and the Ollama/Mesa package versions; other OS
dependencies still resolve from Fedora repositories. The complete installed RPM
list is embedded at `/etc/ollama-image-packages.txt`. For registry-based delivery,
publish a tested image and set `image.repository` plus `image.digest`; do not
reuse a mutable tag for changed runtime code. The repository's release workflow builds
and publishes this image alongside the application images; the Chart itself does not
publish images.

For an offline install, set `model.pullIfMissing=false` in a local values file and use
that file on every upgrade. Install without `--wait`. Then copy a complete Ollama model
store into `/models` in the `ollama` container, copying blobs before the `manifests/`
tree. The store must include the pinned model and all its referenced blobs. The process
waits up to `model.waitSeconds` for the model; readiness remains false until
installation and digest validation complete. Alternatively prepare a PVC first and set
`persistence.existingClaim`.

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

- Startup: wait for the API and optionally install/verify the bootstrap model digest.
- Readiness/liveness: check the process and API. After preparation, no fixed model
  needs to be resident. Unloading, CPU placement or another client's model selection
  does not mark the entire Ollama service unavailable.
- Recovery: normal model eviction never triggers an automatic bootstrap-model reload.
- `helm test` / optional Argo PostSync: explicit bootstrap-model GPU inference checks,
  separate from readiness. These do not establish placement for Review's chosen model.
- Set `model.bootstrap=false` to skip initial model management and its GPU hooks.
  Manage/download models through Ollama independently; Review probes its own selection.

`100% GPU` describes layer placement, not zero CPU use. CPU work and host memory
remain necessary. The readiness check is not an accuracy evaluation or load test.

```bash
kubectl --context minikube -n local-inference get deployment,pods,svc,pvc
```

```bash
kubectl --context minikube -n local-inference logs deployment/review-ollama -c ollama
```

```bash
kubectl --context minikube -n local-inference exec deployment/review-ollama -c ollama -- ollama ps
```

```bash
kubectl --context minikube -n local-inference port-forward service/review-ollama 11434:11434 --address 127.0.0.1
```

Use another local port if host Ollama is listening on 11434. The Service exposes
only the Ollama API; probe port 11435 is not a Service port. There is no API
authentication or Chart-provided network isolation. Use only a trusted local
cluster; introducing NetworkPolicy requires a CNI that enforces it.

## Connect the application

The recommended release/namespace above gives:

```text
http://review-ollama.local-inference.svc.cluster.local:11434
```

Use a backend image from the current source or an approved release snapshot.
Prepare the namespace and stable signing Secret as described in the
[application installation guide](../../../docs/guides/helm-release.md). Keep those
resources and the image values across upgrades. Add the resource overlay after
`values-minikube.yaml`. The command below is for a Helm-managed installation; update
the snapshot through the [GitOps workflow](../../../docs/guides/gitops.md) if Argo CD
manages the application:

```bash
helm upgrade --install local-review deploy/helm/local-review --kube-context minikube --namespace local-review-helm --create-namespace -f /path/to/application-image-values.yaml -f deploy/helm/local-review/values-minikube.yaml -f deploy/helm/local-review/values-minikube-ollama.yaml --wait --timeout 65m
```

Replace `local-review-helm` with your prepared Helm namespace. The application
already defaults to this Service; the optional overlay above
reduces backend resources because the model runs in its own Pod. The Minikube
profile disables NetworkPolicy for the local CNI. With an enforcing CNI, enable
policies: default namespace/release selectors allow access to this Ollama Pod.
The Review model and optional bootstrap model may differ; install Review's chosen
model before restarting its backend. Use standard Helm
commands for krunkit; the optional local build script requires the Docker driver.

## Upgrade, rollback and storage

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
for the bootstrap model through
the Service. The Job is disabled for normal Helm installs, where `helm test` remains
available. See the
[GitOps guide](../../../docs/guides/gitops.md) for first installation and migration.
