# Helm releases and GitHub Actions

`main` owns application source, Dockerfiles, `deploy/helm/local-review`, deployment
tools and workflows. `deployment-release` contains reviewed deployment snapshots.
Do not merge main into deployment-release or edit generated values there: change
the source Chart in main and review the generated release PR.

## Automated release flow

PRs to main must pass `main-ci` before merging; no separate approval is required. After
the merge, the release workflow rechecks main and builds images for any changed backend,
frontend or Ollama components. It publishes those images to GHCR and proposes an
immutable deployment snapshot with their digests. A reviewed deployment PR updates Argo
CD's desired state. See [the GitOps guide](gitops.md) for migration, branch protection,
Argo bootstrap, GPU acceptance and storage/ownership rules.

Format-3 snapshots contain two Charts and their pinned values, Argo Applications,
release metadata and a short README. They contain no source, build workflows or
Python deployment tools. Standard Helm commands remain supported as an alternative
manager; do not use them to upgrade Argo-owned resources.

## Deploy with standard Helm commands

Application deployment uses Helm and kubectl directly. Python, PyYAML and
`scripts/helm_deploy.py` are not deployment prerequisites. The old wrapper remains
for compatibility, but is not needed by this workflow. Terraform may provision the
cluster separately; this Chart does not create a cluster or install Argo CD.

### Prerequisites and target selection

Use Helm 3.17+ or Helm 4, kubectl and an already running Kubernetes cluster (Kubernetes
>=1.30). The provided environment profile targets a single-node Minikube with the Docker
driver and the `standard` StorageClass. Start with 4 CPUs and 8 GiB of cluster memory,
leaving additional host capacity for Docker and the independent Ollama release. The old
2 CPU / 4 GiB cluster setting is too small for the current application requests and
Kubernetes components. Three PVCs request 23 GiB in total; also allow disk space for
images and model downloads.

Run from a separate checkout of the **approved `deployment-release` branch**,
using a new namespace for the first Helm installation:

```bash
export REVIEW_CONTEXT=tf-lab
export REVIEW_NAMESPACE=local-review-helm
kubectl config get-contexts "$REVIEW_CONTEXT"
kubectl --context "$REVIEW_CONTEXT" get nodes
kubectl --context "$REVIEW_CONTEXT" get storageclass standard
```

Replace `tf-lab` with your existing kubeconfig context. Every cluster command
below specifies the target; none switches the global context. Add an explicit
`--kubeconfig /path/to/config` to Helm and kubectl if needed. Do not reuse a
namespace containing resources managed by the old deployment scripts.

Install the [independent Ollama release](../../deploy/helm/local-ollama/README.md)
first, as `review-ollama` in `local-inference`, serving the pinned `qwen3:1.7b` model.
The default endpoint is `http://review-ollama.local-inference.svc.cluster.local:11434`.
The application Chart connects to this Service; it does not install Ollama itself. The
application validates its selected model digest, capabilities and context at startup.
Only Ollama needs registry access to download a missing model.

### One-time namespace and Secret preparation

Create the new namespace before its Secrets:

```bash
kubectl --context "$REVIEW_CONTEXT" create namespace "$REVIEW_NAMESPACE"
```

Create the signing Secret once, with a stable `SIGNING_SECRET` of at least
32 characters. This example generates a private temporary file, creates the
Secret without putting the key on the command line, then removes the file:

```bash
(
  set -eu
  umask 077
  review_secret_file="$(mktemp)"
  trap 'rm -f "$review_secret_file"' EXIT
  openssl rand -hex 32 > "$review_secret_file"
  kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" \
    create secret generic review-secrets \
    --from-file="SIGNING_SECRET=$review_secret_file"
)
```

Run this only for a new installation. `create` refuses to overwrite an existing
Secret; normal upgrades reuse the existing key. Existing deployments must retain
their original key. The Chart references this Secret and never generates or
rotates signing material. Do not commit secret files or credentials.

For private GHCR images, prepare an image pull Secret in the same namespace. Use a
private Docker configuration file with valid GHCR credentials in its `auths` field. A
file that only references a local credential helper is insufficient:

```bash
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" \
  create secret generic ghcr-pull \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson=/secure/path/ghcr-config.json
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
export REVIEW_VALUES="$HOME/.config/local-qwen/values-minikube.yaml"
cp deploy/helm/local-review/values-minikube.yaml "$REVIEW_VALUES"
```

Only make this copy on initial setup; keep your existing environment file on
upgrades. For private images, set `imagePullSecrets: [{name: ghcr-pull}]` in that file.
Other supported overrides include `signingSecret.existingSecret`,
`persistence.storageClass`, `persistence.*.existingClaim`, and `model.ollamaBaseUrl`.
The backend currently accepts local HTTP Ollama endpoints on port 11434; arbitrary
remote DNS names are not supported by its runtime configuration validation.
Do not override release image digests in the environment file.

The Minikube example explicitly sets `networkPolicy.enabled: false`. It provides
**no NetworkPolicy isolation**, and is intended for local learning. To enable
policies, use an enforcing CNI and enable the default cluster selectors:

```yaml
networkPolicy:
  enabled: true
  ollamaNamespace: local-inference
  ollamaRelease: review-ollama
```

If changing release or namespace, update `model.ollamaBaseUrl` to match. For host
Ollama instead, explicitly set the URL to `http://host.minikube.internal:11434`,
clear `networkPolicy.ollamaNamespace`, and supply `networkPolicy.ollamaHostCidr`
as the actual private host IPv4 `/32`. Helm does not discover host addresses.

Inspect the approved configuration without contacting the cluster:

```bash
helm lint ./deploy/helm/local-review \
  -f release-values.yaml -f "$REVIEW_VALUES"
helm template local-review ./deploy/helm/local-review \
  --namespace "$REVIEW_NAMESPACE" \
  -f release-values.yaml -f "$REVIEW_VALUES"
```

Install or upgrade using the same inputs:

```bash
helm upgrade --install local-review ./deploy/helm/local-review \
  --kube-context "$REVIEW_CONTEXT" \
  --namespace "$REVIEW_NAMESPACE" \
  --create-namespace \
  -f release-values.yaml \
  -f "$REVIEW_VALUES" \
  --wait --timeout 65m
```

Always pass both values files when upgrading to another approved snapshot. Helm does not
automatically load `release-values.yaml`. Without it, Chart defaults reference local
development images, not the published release. On main, use the provided Minikube file
for offline linting and rendering. To install from main, you must also supply or load
the application images.

The startup budget defaults to 3600 seconds; the 65-minute Helm timeout allows
additional scheduling and initialization time. A timeout leaves resources for
inspection. `--wait` checks Kubernetes readiness, not a completed model review.
The Chart uses one release per namespace, single replicas and `Recreate` updates,
so upgrades briefly interrupt availability. Prefer upgrading when idle.

Open the application with a foreground loopback port-forward:

```bash
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" \
  port-forward --address 127.0.0.1 service/review-frontend 8080:8080
```

Visit **http://localhost:8080**, register/log in, and complete a real review.
If changing the local port, also change `config.allowedOrigin` in the environment
file, run the Helm upgrade, and use the matching forwarding port. Origin matching
is exact; do not substitute `127.0.0.1` in the browser URL.

### Argo CD integration

Argo CD can render the same Chart with release values followed by environment
overrides. The [GitOps guide](gitops.md) covers installation, first sync and migration. Templates use no random secret generation, host discovery,
cluster `lookup`, or deployment hooks. Prepare external Secrets independently.
Argo CD uses Helm to render resources and owns their synchronization; its instances
are not ordinary Helm releases. Use one manager for each deployment, and validate
PVC deletion/retention behavior before enabling automatic pruning. Installing
Argo CD and transferring ownership are outside this Helm setup.

## Existing Kustomize deployment migration

The legacy Kustomize scripts remain available during transition. They must never
manage workloads already transferred to Helm. Standard Helm ownership checks
reject resources owned by another manager; do not bypass them with takeover flags.

1. Record the old profile, namespace, ownership state and signing Secret; take
   consistent backups of SQLite and DynamoDB while workloads are stopped. A PVC
   is persistence, not a backup.
2. Use the existing owned `scripts/minikube_demo.sh legacy undeploy --profile NAME` flow without
   `--purge-data`. It retains the three PVCs and signing Secret.
3. Supply the retained claims explicitly:

   ```yaml
   persistence:
     history:
       existingClaim: review-history
     modelCache:
       existingClaim: review-model-cache
     dynamodb:
       existingClaim: review-dynamodb
   signingSecret:
     existingSecret: review-secrets
   ```

4. Install the reviewed Helm release with the same profile/namespace and this
   values file. No PVC adoption or key rotation is required. Keep the overrides
   on subsequent upgrades; the Chart will not manage external claims.
5. Verify login/history preservation and a complete review, then use Helm tooling
   for that deployment. Retain old ownership records for recovery evidence.

## Status, uninstall and rollback

```bash
helm status local-review --kube-context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE"
helm history local-review --kube-context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE"
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" get pods,pvc
kubectl --context "$REVIEW_CONTEXT" -n "$REVIEW_NAMESPACE" get events --sort-by=.metadata.creationTimestamp

# Only when you intend to uninstall:
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
helm rollback local-review REVISION --kube-context "$REVIEW_CONTEXT" \
  -n "$REVIEW_NAMESPACE" --wait --timeout 65m
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

## Verification

Deployment needs only Helm/kubectl. The optional CI checks additionally use
Python and PyYAML:

```bash
python scripts/validate_helm.py --snapshot
```

On main, run `python scripts/validate_helm.py` and
`python -m pytest scripts/tests/test_helm_release.py -q`. These render the local
profile, explicitly enabled network policies and operation without a
cluster. CI also validates Kubernetes schemas for these profiles.

Source CI also checks source Chart variants, configuration rollouts, PVC retention,
single-worker constraints, cumulative changes, image reuse/provenance, malformed
digests, immutable version/retry behavior, failed-candidate preservation, stale
base rejection, supersession and explicit target selection. Offline checks do not prove real model
inference, multi-architecture image execution or the behavior of a particular CNI;
those are separate environment acceptance checks.
