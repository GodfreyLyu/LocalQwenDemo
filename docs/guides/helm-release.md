# Helm releases and GitHub Actions

`main` owns application source, Dockerfiles, `deploy/helm/local-review`, deployment
tools and workflows. `deployment-release` contains reviewed deployment snapshots.
Do not merge main into deployment-release or edit generated values there: change
the source Chart in main and review the generated release PR.

## Automated release flow

Every push to main calls the source quality workflow, fingerprints the actual
component inputs, builds changed images for linux/amd64 and linux/arm64, pushes
them to GHCR, and renders and validates a snapshot. It creates a versioned branch,
for example `release-candidate/0.1.0-rc.42`, and a PR to `deployment-release`.
Only a reviewed PR can update the approved release branch.

The version combines main's stable Chart version and the Release candidate
workflow's `GITHUB_RUN_NUMBER`. Failed or no-op runs can leave gaps; a rerun keeps
the same number. The branch is created atomically and never updated, force-pushed,
rebased or deleted. Another change requires another workflow run/version.

The initial release builds both images. Later releases retain each unchanged
image's digest and original source SHA. Configuration-only updates reuse images;
documentation outside the published deployment inputs produces no empty PR.
Fingerprints compare with the newest validated open candidate on the current
release base, or the approved release, so failed/cancelled intermediate runs do
not lose changes. Source quality runs even without a deployable difference.

A candidate is one commit based on the current deployment-release and contains
only the Chart, release values, provenance, deployment tools and this guide.
Application source trees are not copied. CI validates the exact candidate branch head and its
release base, posts `deployment/snapshot`, and only then closes older automatic
PRs with a link to the replacement. Their branches remain available for audit.
Until validation succeeds, the previous PR stays open; a failed candidate never
replaces it. Existing legacy `automation/deployment-release` PRs follow this same
migration rule. Supersession itself is a separate job and can be retried.

`release.json` format 2 records the main SHA, input fingerprints, image source
SHAs/digests, version, branch, workflow run ID/number and release base SHA. The
Chart version matches the candidate version and appVersion identifies main's
commit. Legacy format 1 remains readable for migration. Merging a PR approves
a snapshot; deploying it to a selected cluster remains an explicit operation.

### One-time repository setup

1. Merge the implementation PR into main.
2. A maintainer with repository and workflow access runs:

   ```bash
   python3 scripts/release/bootstrap.py --repository GodfreyLyu/LocalQwenDemo
   ```

   This creates an independent deployment-release branch with its validation
   entrypoint. It is idempotent and never overwrites an existing branch. The
   bootstrap entrypoint calls the trusted validation workflow on main; it is
   preserved by the release generator, so GITHUB_TOKEN need not edit workflows.
3. In repository Actions settings, allow Actions to create pull requests. Workflow
   jobs explicitly request only their required contents/packages/PR/status access.
4. Run **Release candidate** on main to produce the first candidate if the initial
   push preceded bootstrap. No personal token or cloud account is required.
5. Review package visibility in GHCR. Public repository visibility does not imply
   public package visibility. For private packages, create an image pull Secret in
   the deployment namespace and supply `imagePullSecrets: [{name: ghcr-pull}]`.

Repository protection is recorded in `deploy/release/branch-protection.json`:
require one approving review, dismiss stale approvals, require `deployment/snapshot`
from GitHub Actions, require an up-to-date base, enforce rules for administrators,
and block force pushes and deletion. The candidate ruleset in
`deploy/release/candidate-ruleset.json` prevents updates and deletion of
`release-candidate/*` with no bypass actors; creation is allowed. Keep automatic
branch deletion disabled. Maintainers apply these settings using the repository
administration API; the release workflow has no administration access.

```bash
gh api --method PUT repos/GodfreyLyu/LocalQwenDemo/branches/deployment-release/protection \
  --input deploy/release/branch-protection.json
gh api --method POST repos/GodfreyLyu/LocalQwenDemo/rulesets \
  --input deploy/release/candidate-ruleset.json
```

Create the ruleset once; update its existing ID when changing the policy.
The release workflow invokes validation directly, so it does not depend on a
GITHUB_TOKEN-created PR triggering another workflow. The bootstrap PR entrypoint
also invokes validation, using trusted main code and treating snapshots as data.
The existing public-repository entrypoint remains unchanged during migration.

Rerun a failed **Release candidate** run to recover build or PR creation failures.
Once its version branch exists, retry verifies and reuses its exact snapshot,
commit, digests and PR, including when `rebuild` was selected. It refuses content,
run identity or release-base drift and never reopens a rejected/merged PR. If
main or the approved base has changed, start a **new** workflow run on current
main. Do not use GitHub's Update branch button on immutable candidates.

To intentionally rebuild images, start a new manual run with `rebuild=true`.
To retry validation alone, dispatch **Deployment validation** on main with the
full candidate SHA. This only validates; rerun the release workflow's failed jobs
to complete pending supersession. An unchanged validated open candidate is
reused and revalidated without creating an empty PR.

For building local source images with an optional convenience CLI, see the
[local Helm workflow](minikube-demo.md). Published snapshots use the standard
Helm commands below; they do not require the local development scripts.

## Deploy with standard Helm commands

Application deployment uses Helm and kubectl directly. Python, PyYAML and
`scripts/helm_deploy.py` are not deployment prerequisites. The old wrapper remains
for compatibility, but is not needed by this workflow. Terraform may provision the
cluster separately; this Chart does not create a cluster or install Argo CD.

### Prerequisites and target selection

Use Helm 3.17+ or Helm 4, kubectl and an already running Kubernetes cluster
(Kubernetes >=1.30). The provided environment profile targets a single-node
Minikube with the Docker driver and the `standard` StorageClass. Start with
4 CPUs and 8 GiB of cluster memory, leaving additional host capacity for Docker
and the independent Ollama release. The old 2 CPU / 4 GiB cluster setting cannot fit the current
application requests plus Kubernetes. Three PVCs request 23 GiB in total; also
allow disk space for images and model downloads.

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
The application Chart connects to this Service; it does not install Ollama itself.
The application validates the model digest and tokenizer at startup. Both inference
modes may need outbound HTTPS for tokenizer/model initialization.

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

For private GHCR images, also prepare an image pull Secret in the same namespace.
For example, use a private Docker configuration file containing usable GHCR
`auths` credentials (a file containing only a local credential-helper reference
is insufficient):

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

Always pass both values files when upgrading to another approved snapshot.
Helm does not automatically load `release-values.yaml`. Without it, Chart defaults
reference local development images, not the published release. On main, use the
provided Minikube file for offline lint/render; installing from main additionally
requires explicitly supplying or loading actual application images.

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

### Future Argo CD integration

The same Chart can be rendered by Argo CD with the release values followed by
environment overrides. Templates use no random secret generation, host discovery,
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

For an audited rollback, revert the intended application/Chart/configuration
changes through a PR to main. CI then produces a new versioned candidate against
the current approved release; review, merge and explicitly deploy that snapshot.
Do not restore an old release.json verbatim: its version and release base belong
to an earlier candidate. Reuse retained PVCs/Secret. A rollback does not undo
database changes; verify compatibility before deploying earlier application code.

## Verification

Deployment needs only Helm/kubectl. The optional CI checks additionally use
Python and PyYAML:

```bash
python scripts/validate_helm.py --snapshot
```

On main, run `python scripts/validate_helm.py` and
`python -m pytest scripts/tests/test_helm_release.py -q`. These render the local
profile, explicitly enabled network policies and Transformers mode without a
cluster. CI also validates Kubernetes schemas for these profiles.

Source CI also checks source Chart variants, configuration rollouts, PVC retention,
single-worker constraints, cumulative changes, image reuse/provenance, malformed
digests, immutable version/retry behavior, failed-candidate preservation, stale
base rejection, supersession and explicit target selection. Offline checks do not prove real model
inference, multi-architecture image execution or the behavior of a particular CNI;
those are separate environment acceptance checks.
