# Helm releases and GitHub Actions

`main` owns application source, Dockerfiles, both Charts under `deploy/helm`, deployment
tools and workflows. `deployment-release` contains reviewed deployment snapshots.
Do not merge main into deployment-release or edit generated values there: change
the source Chart in main and review the generated release PR. The dedicated rollback
workflow can also restore an approved snapshot with new release metadata.

## Automated release flow

The repository's protection configuration requires `main-ci` before merging to main,
with no required review approvals. After the merge, the release workflow rechecks
main and builds images for any changed backend,
frontend or Ollama components. It publishes those images to GHCR and proposes an
immutable deployment snapshot with their digests. A reviewed deployment PR updates Argo
CD's desired state. See [the GitOps guide](gitops.md) for branch protection, Argo
bootstrap, GPU acceptance and storage ownership.

Format-3 snapshots contain two Charts and their pinned values, Argo Applications,
release metadata and a short README. They contain no source, build workflows or
Python deployment tools. Standard Helm commands remain supported as an alternative
manager; do not use them to upgrade Argo-owned resources.

## Deploy with standard Helm commands

Application deployment uses Helm and kubectl directly. Python and PyYAML are not
deployment prerequisites. Start the cluster separately with Minikube. The repository's
Terraform module manages platform resources in that existing cluster; this Chart
does not create a cluster or install Argo CD.

### Prerequisites and target selection

Use Helm 3.17+ or Helm 4, kubectl and an already running Kubernetes cluster (Kubernetes
>=1.30). The provided values use Minikube's `standard` StorageClass and work with
standard Helm on krunkit or Docker-driver clusters. The independent GPU Ollama image
requires ARM64 krunkit; the optional local image-build CLI requires the Docker driver.
For the default application resource settings, start with 4 CPUs and 8 GiB of cluster
memory. Check available capacity for Ollama, platform services and the host before
installing. The application requests 11 GiB across two PVCs; Ollama adds a 4 GiB PVC.
Allow additional disk space for images and any monitoring storage. The
`values-minikube-ollama.yaml` overlay offers smaller backend resource settings when
inference runs in the independent Ollama service.

Run from a separate checkout of the **approved `deployment-release` branch**,
using a new namespace for the first Helm installation:

```bash
export REVIEW_CONTEXT=minikube
```

```bash
export REVIEW_NAMESPACE=local-review-helm
```

```bash
kubectl config get-contexts "$REVIEW_CONTEXT"
```

```bash
kubectl --context "$REVIEW_CONTEXT" get nodes
```

```bash
kubectl --context "$REVIEW_CONTEXT" get storageclass standard
```

Replace `minikube` if your cluster uses a different kubeconfig context. Every cluster
command below specifies the target; none switches the global context. Add an explicit
`--kubeconfig /path/to/config` to Helm and kubectl if needed. Do not reuse a
namespace containing resources managed by the old deployment scripts.

Install the [published Ollama release](../../deploy/helm/local-ollama/README.md#install-a-published-release)
first, as `review-ollama` in `local-inference`, serving the pinned `qwen3:1.7b` model.
Use the snapshot's Ollama `values-release.yaml`. Private images need a pull Secret
in `local-inference` as well as the separate application namespace.
The existing `review-ollama` release now runs the independently packaged inference
image: one entry point supervises Ollama and the generic Thrift server. The backend
connects directly to `review-ollama.local-inference.svc.cluster.local:9091`. The
application chart no longer installs a separate RPC worker. Native Ollama HTTP stays
on loopback inside inference. See [coordinated migration/rollback](../operations/inference-migration.md)
before upgrading an existing installation; preserve PVCs, secrets and ownership.

### One-time namespace and Secret preparation

Create the new namespace before its Secrets:

```bash
kubectl --context "$REVIEW_CONTEXT" create namespace "$REVIEW_NAMESPACE"
```

Create the signing Secret once, with a stable `SIGNING_SECRET` of at least
32 characters. Run these steps in the same terminal. `mktemp` creates a private
temporary file so the generated key does not appear in the command or terminal output:

```bash
review_secret_file="$(mktemp)"
```

```bash
openssl rand -hex 32 > "$review_secret_file"
```

After generation succeeds, create the Secret:

```bash
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" create secret generic review-secrets --from-file="SIGNING_SECRET=$review_secret_file"
```

Remove the temporary file after the creation attempt, including if the command fails:

```bash
rm -f "$review_secret_file"
```

Run this only for a new installation. `create` refuses to overwrite an existing
Secret; normal upgrades reuse the existing key. Existing deployments must retain
their original key. The Chart references this Secret and never generates or
rotates signing material. Do not commit secret files or credentials.

For private GHCR images, prepare an image pull Secret in the same namespace. Use a
private Docker configuration file with valid GHCR credentials in its `auths` field. A
file that only references a local credential helper is insufficient:

```bash
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" create secret generic ghcr-pull --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=/secure/path/ghcr-config.json
```

Public images do not require this Secret. Git repository credentials and image
registry credentials are separate concerns.

### Configure, inspect and install

There are three configuration layers, applied in this order:

1. Chart `values.yaml`: application defaults.
2. Root `release-values.yaml`: generated, reviewed image digests and source SHA.
3. Your `values-minikube.yaml`: environment settings and Secret references.

The environment example ships **inside the Chart**, so it is also included in
every generated release snapshot. Copy it outside the checkout:

```bash
mkdir -p "$HOME/.config/local-qwen"
```

```bash
export REVIEW_VALUES="$HOME/.config/local-qwen/values-minikube.yaml"
```

```bash
cp deploy/helm/local-review/values-minikube.yaml "$REVIEW_VALUES"
```

Only make this copy on initial setup; keep your existing environment file on
upgrades. For private images, set `imagePullSecrets: [{name: ghcr-pull}]` in that file.
Other supported overrides include `signingSecret.existingSecret`,
`persistence.storageClass`, `persistence.*.existingClaim` and `inference.host/port`.
Application model identity assertions use `model.expectedId/expectedRevision`.
Execution policy belongs to the inference chart, not a shared backend ConfigMap.
Do not override release image digests in the environment file.

The Minikube application overlay disables its NetworkPolicy. For isolation, use an
enforcing CNI and enable `networkPolicy.enabled`, with `inferenceNamespace` and
`inferenceRelease` selecting the actual model release. Match `inference.host` to
that release's fully qualified Service name. Configure the inference chart's
`networkPolicy.backendNamespace/backendRelease` to accept the selected application
namespace/release; this is required for the custom Helm namespace used here.
Native 11434 is never exposed across Pods. Model downloads use inference-only
registry egress when enabled.

Inspect the approved configuration without contacting the cluster:

```bash
helm lint ./deploy/helm/local-review -f release-values.yaml -f "$REVIEW_VALUES"
```

```bash
helm template local-review ./deploy/helm/local-review --namespace "$REVIEW_NAMESPACE" -f release-values.yaml -f "$REVIEW_VALUES"
```

Install or upgrade using the same inputs:

```bash
helm upgrade --install local-review ./deploy/helm/local-review --kube-context "$REVIEW_CONTEXT" --namespace "$REVIEW_NAMESPACE" --create-namespace -f release-values.yaml -f "$REVIEW_VALUES" --wait --timeout 65m
```

Always pass both values files when upgrading to another approved snapshot. Helm does not
automatically load `release-values.yaml`. Without it, Chart defaults reference local
development images, not the published release. On main, use the provided Minikube file
for offline linting and rendering. To install from main, you must also supply or load
the application images.

The startup budget defaults to 3600 seconds; the worker opens its listener only
after validating Ollama, and the backend checks the service's model identity and
readiness. That handshake does not require an empty service queue. The
65-minute Helm timeout allows
additional scheduling and initialization time. A timeout leaves resources for
inspection. `--wait` checks Kubernetes readiness, not a completed model review.
The Chart uses one release per namespace, single replicas and `Recreate` updates,
so upgrades briefly interrupt availability. Prefer upgrading when idle.

Open the application with a foreground loopback port-forward:

```bash
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" port-forward --address 127.0.0.1 service/review-frontend 8080:8080
```

Visit **http://localhost:8080**, register/log in, and complete a real review.
If changing the local port, also change `config.allowedOrigin` in the environment
file, run the Helm upgrade, and use the matching forwarding port. Origin matching
is exact; do not substitute `127.0.0.1` in the browser URL.

For deployments in the default `local-review-demo` namespace, the
[background endpoint launcher](../../scripts/README.md#background-minikube-endpoints)
can forward the application and this repository's platform services together.
Run it from the main source checkout with Python 3.10+ and kubectl; generated
deployment snapshots do not include it. Its namespaces are fixed, so use the
explicit foreground command above for the `local-review-helm` namespace used in
this guide. Avoid running both forwarding options on the same local port.

### Argo CD integration

Argo CD can render the same Chart with release values followed by environment
overrides. The [GitOps guide](gitops.md) covers installation, first sync and migration. Templates use no random secret generation, host discovery,
cluster `lookup`, or deployment hooks. Prepare external Secrets independently.
Argo CD uses Helm to render resources and owns their synchronization; its instances
are not ordinary Helm releases. Use one manager for each deployment, and validate
PVC deletion/retention behavior before enabling automatic pruning. Installing
Argo CD and transferring ownership are outside this Helm setup.

## Status, uninstall and rollback

```bash
helm status local-review --kube-context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE"
```

```bash
helm history local-review --kube-context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE"
```

```bash
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" get pods,pvc
```

```bash
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" get events --sort-by=.metadata.creationTimestamp
```

When you intend to remove the application, uninstall the release:

```bash
helm uninstall local-review --kube-context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" --wait
```

Uninstall removes Helm-managed application resources but keeps Chart-created PVCs
using `helm.sh/resource-policy: keep`. Externally managed claims and the signing
Secret are not part of the release. The namespace is retained. A retained claim
is not automatically portable across namespaces or clusters, and StorageClass
changes do not migrate existing data. For reinstallation, explicitly reference
retained claims with `persistence.*.existingClaim` and keep the original signing Secret.

For an immediate rollback of a Helm-managed release, inspect `helm history`, then
replace `REVISION` with the chosen revision:

```bash
helm rollback local-review REVISION --kube-context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" --wait --timeout 65m
```

A Helm rollback restores the saved release configuration, not external Secret contents
or database data. Reconcile the approved release afterward so the next upgrade
does not unintentionally reintroduce the reverted change.

For an audited rollback, open a PR to main that reverts the relevant application code,
Chart or configuration changes. CI then produces a new versioned candidate against the
current approved release; review, merge and explicitly deploy that snapshot. Do not
restore an old release.json verbatim: its version and release base belong to an earlier
candidate. Reuse the retained PVCs and signing Secret. A rollback does not undo database
changes; verify compatibility before deploying earlier application code.

For urgent recovery of an Argo-managed deployment, first create a rollback deployment
PR from a previously approved tag, then fix/revert the source in main. Follow the
[release rollback runbook](release-rollback.md). Pause ordinary deployment PR merges
manually while recovering; keep main CI and Argo auto-sync enabled.

## Verification

Deployment needs only Helm and kubectl. Python validation runs from a **main source
checkout** with the development environment installed. Release snapshots contain no
Python scripts. To check an approved snapshot, point the source validator at its
separate checkout:

```bash
backend/.venv/bin/python scripts/validate_helm.py --root /path/to/deployment-release-checkout --snapshot
```

To check the main source Charts and release tooling, run:

```bash
backend/.venv/bin/python scripts/validate_helm.py
```

```bash
backend/.venv/bin/python -m pytest scripts/tests/release -q
```

These checks cover Chart rendering, resource and storage constraints, and release
generation and validation without a cluster. CI also validates Kubernetes schemas.
See the [testing guide](../testing/README.md) for the full check suite. Real inference,
image execution on each architecture and CNI enforcement need separate acceptance
checks in the target environment.
