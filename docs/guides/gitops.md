# PR-gated images and Argo CD

The checked-in branch protection settings require PRs for changes to `main` and
`deployment-release`. The `Quality checks` workflow supplies `main-ci`, which passes
only when its application and configuration jobs both succeed. Release PRs require
`deployment/snapshot`. Both branches require an up-to-date PR and resolved
conversations, apply the same rules to administrators, and require no review approvals.
Authors can merge their own PRs once the required checks pass.

After merge, `Prepare release` rechecks the exact main commit, builds changed
backend/frontend images for amd64+arm64 and Ollama for arm64, and pushes to GHCR.
Ollama's image contains the Vulkan runtime, not model weights. Every deployment
uses a digest. Unchanged components reuse both their digest and original source
SHA. Chart-only changes do not rebuild images. Backend-only changes preserve the
Ollama Chart version, ConfigMap checksum and Pod template.

The workflow opens an immutable `release-candidate/*` PR to `deployment-release`.
It validates the exact candidate directly, independent of PR event delivery.
The main-owned `release-pr-validation.yml` also validates same-repository release
PRs through `pull_request_target`; candidate files are rendered as data and never
executed. Its reusable validator explicitly checks out main. See GitHub's
[trusted default-branch behavior](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target).

Merging the reviewed deployment PR updates the desired state Argo CD watches. CI has no
kubeconfig or Minikube access, so it cannot verify GPU readiness. A successful image
build is not evidence of working GPU inference.

After merge, **Tag approved release** records the exact deployment commit and its
main source commit with paired immutable tags. For incident recovery, manually pause
ordinary deployment PR merges and generate a rollback candidate from an approved
deployment tag. Keep CI and Argo sync running. See the
[release tags and rollback runbook](release-rollback.md) for setup, historical tag
backfill, recovery checks and the subsequent main fix/revert PR.

## Repository release state

The release workflow uses format-3 snapshots. Run **Prepare release** on main, then
review and merge its candidate after `deployment/snapshot` succeeds. The one-time
branch bootstrap tools are no longer part of the current workflow.

The generator removes obsolete scripts and docs when generating snapshots.
Release files are limited to the two Helm Charts, `deploy/argocd`,
pinned values, `release.json` and a short README. The root `release-values.yaml` is
retained for existing Helm commands and is checked to be identical to local-review's
Chart-local `values-release.yaml`.

Apply repository protection using a maintainer identity:

```bash
gh api --method PUT repos/GodfreyLyu/LocalQwenDemo/branches/main/protection --input deploy/release/main-protection.json
```

```bash
gh api --method PUT repos/GodfreyLyu/LocalQwenDemo/branches/deployment-release/protection --input deploy/release/branch-protection.json
```

Keep the existing immutable candidate ruleset. Do not enable automatic deletion
of merged branches. Do not use administrator merges to bypass either PR gate.

## Argo CD installation and first deployment

Terraform owns the platform namespaces, device plugin and optional Argo CD and
monitoring Helm releases. First set `argocd_enabled = true` and leave
`monitoring_enabled = false` in your ignored platform `terraform.tfvars`.
Run init, plan and apply as documented in the
[platform README](../../infra/local-platform/README.md). This creates the platform
namespaces and installs Argo CD. Argo CD uses the Chart version pinned in the platform
configuration, ClusterIP access and annotation-based resource tracking.
ApplicationSets, Dex and notifications are disabled for the local cluster.

Next create the `local-inference` and `local-review-demo` namespaces, then prepare
the stable `review-secrets` Secret in `local-review-demo` using the
[Helm guide's Secret steps](helm-release.md#one-time-namespace-and-secret-preparation).
Use `REVIEW_CONTEXT=minikube` and `REVIEW_NAMESPACE=local-review-demo` for that example.
These application namespaces are not created by Terraform's default platform
configuration or by the limited Argo AppProject.

Private GHCR packages additionally require pull credentials in **both** application namespaces,
configured in each Chart's environment values. Access to a private Git repository
requires a separate Argo CD repository Secret. None of these credentials belongs in Git.
For public packages, no image pull Secret is needed. Verify GHCR package visibility
before the first sync; repository visibility alone does not set package visibility.

With the namespaces in place, create the external Grafana admin Secret in `monitoring`,
set `monitoring_enabled = true`, and follow the
[platform monitoring setup](../../infra/local-platform/README.md#monitoring).
Grafana's Role and RoleBinding need `local-review-demo` to exist before this apply.
Wait for the monitoring services and ServiceMonitor/PrometheusRule CRDs before the
first application sync.

The supplied Argo Application enables `values-monitoring.yaml` after its release,
Minikube and Ollama values. Terraform owns the monitoring platform and CRDs; Argo owns
the application monitors, rules and dashboard ConfigMaps. The application AppProject
has no cluster-wide access. Standard Helm deployments can omit this overlay when
monitoring is not needed.

Finally, run the bootstrap commands from a separate checkout of the
**approved format-3 deployment-release snapshot**:

```bash
kubectl --context minikube apply -f deploy/argocd/project.yaml
```

```bash
kubectl --context minikube apply -f deploy/argocd/review-ollama-application.yaml
```

```bash
kubectl --context minikube -n argocd wait application/review-ollama --for=jsonpath='{.status.operationState.phase}'=Succeeded --timeout=2400s
```

```bash
kubectl --context minikube -n argocd wait application/review-ollama --for=jsonpath='{.status.health.status}'=Healthy --timeout=2400s
```

```bash
kubectl --context minikube apply -f deploy/argocd/local-review-application.yaml
```

```bash
kubectl --context minikube -n argocd wait application/local-review --for=jsonpath='{.status.health.status}'=Healthy --timeout=2400s
```

Both apps automatically track deployment-release, prune obsolete workloads and self-heal
drift. Their names match Helm release names to preserve labels and the Ollama Service
URL. Follow the order shown above for the first deployment. Later updates to the two
Applications are independent and are not applied as one transaction. Backend readiness
checks, restarts and sync retries handle temporary Ollama unavailability.
Before approving the first release that removes the old backend cache, follow the
[cache retirement step](#retire-the-legacy-backend-cache) below.

After verifying the child Applications, run the following command from the main source
checkout:

```bash
kubectl --context minikube apply -f infra/local-platform/kubernetes/argocd/root-application.yaml
```

This small root app watches only deploy/argocd so later approved Application
configuration updates also reconcile. It uses the bootstrap/default project,
does not prune child Applications, and has no cascading deletion finalizer.
Restrict write access to the release branch because this is a control-plane entry.
The child AppProject permits only the two application namespaces and required
workload kinds. Routine application sync cannot modify the GPU plugin or Argo CD.

## Access the application and platform

Once the services are ready, run the endpoint launcher from the root of the main
source checkout. It requires Python 3.10+ and kubectl; it is not included in
generated deployment snapshots:

```bash
python3 scripts/minikube_endpoints.py
```

The command starts background forwards for this repository's review app, backend,
DynamoDB, Ollama, Argo CD, Grafana, Prometheus and Alertmanager. It prints their
local addresses and forwarding states, then returns. Other projects' services in
the same cluster are excluded. Open the printed frontend URL, normally
`http://localhost:8080`, to use the application.

See the [endpoint guide](../../scripts/README.md#background-minikube-endpoints)
for status and stop commands, port settings, skipped services and logs. The
launcher uses the fixed namespaces in that guide and does not change Argo CD
resources or the global kubectl context.

## Verification, storage and ownership

The Ollama Application skips Helm test hooks and enables a separate `PostSync` Job when
model bootstrap is enabled. The Job calls the Service to generate text and verify the
bootstrap model, digest, and full GPU placement. It does not request a second GPU slot.
A failed Job fails sync. Service readiness checks the process and API after initial
preparation; it does not require a fixed model to remain loaded or on the GPU. The
review backend separately validates its selected model. Standard Helm installs support
`helm test` and do not render the Argo Job unless enabled explicitly. See
[Argo Helm hooks](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/).

### Retire the legacy backend cache

For an existing Argo-managed installation, prepare the old `review-model-cache` PVC
before merging the deployment PR that removes its template. Fresh installations and
Helm-only deployments do not need this step. If an external cache claim was used,
leave it with its original owner.

Inspect the old claim using the actual application context and namespace:

```bash
kubectl --context minikube -n local-review-demo get pvc review-model-cache -o yaml
```

If it is absent, there is nothing to migrate. Otherwise verify it belongs to the
`local-review` application, is Bound, and retains `helm.sh/resource-policy: keep`
and `argocd.argoproj.io/sync-options: Prune=false,Delete=false`. Record its UID and
volume name for the post-upgrade check, then add this annotation to that claim only:

```bash
kubectl --context minikube -n local-review-demo annotate pvc review-model-cache argocd.argoproj.io/compare-options=IgnoreExtraneous --overwrite
```

This leaves data and retention settings intact. Without it, a retained resource
removed from Git leaves the application OutOfSync: `Prune=false` prevents deletion,
while `IgnoreExtraneous` excludes it from sync comparison. See Argo CD's
[prune behavior](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#no-prune-resources)
and [compare options](https://argo-cd.readthedocs.io/en/stable/user-guide/compare-options/).
After the approved release syncs, verify the claim still has the same UID and volume,
and the application is Synced and Healthy. The first cleanup upgrade changes Pod
templates and performs the normal Recreate rollout; later configuration changes
restart only the workloads that consume that configuration.

### Storage retention

The three application and Ollama PVCs have the Helm keep annotation and the Argo
`Prune=false,Delete=false` annotations. Deleting a namespace or cluster still deletes its storage; these
annotations are not backups. Bootstrap does not delete or recreate namespaces or signing
keys. Before applying an Application for an existing Helm-managed workload, record its
exact values, image digests and PVCs. Plan the end of Helm management and verify that
Argo renders matching resources before transferring ownership. Never let Helm and Argo
upgrade one workload concurrently, or blindly uninstall a release to perform migration.
Older backend cache PVCs retain their existing protection even though current Charts
no longer create or mount them; see the [storage reference](../reference/architecture.md#storage).

Finish acceptance by submitting a real code review through the frontend and
checking saved history. GPU admission is not model-quality or load acceptance.
Roll back through a reviewed Git change restoring earlier digests/configuration;
with autosync enabled, an imperative Helm rollback is not the source of truth.
