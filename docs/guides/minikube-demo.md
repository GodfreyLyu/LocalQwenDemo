# Local Minikube development with Helm

`scripts/minikube_demo.sh` is an optional local development CLI. It builds source
images and loads them into an **existing** Docker-driver Minikube, then invokes
standard Helm. The application Chart is the same one used by
[direct Helm deployment](helm-release.md). The script does not start, resize or
delete clusters, install Argo CD, discover host IPs, or operate on Argo CD instances.

Old Kustomize deployments use the explicit `legacy` entry described in
[minikube-legacy.md](minikube-legacy.md). There is no automatic ownership takeover.

## Prerequisites

Install the Python environment described in [local development](local-development.md),
Helm 3.17+ or Helm 4, kubectl, Docker and Minikube. Use an existing single-node
Docker-driver profile, a usable `standard` StorageClass, and reachable host Ollama
with the Chart's pinned model. The local profile disables NetworkPolicy isolation.
If enabling policies in your own values, supply the actual private host IPv4 `/32`
and use an enforcing CNI. No network rule is discovered or changed implicitly.

Start with 4 CPUs / 8 GiB for one application, leaving host capacity for Ollama.
`doctor` considers both node allocatable resources and the Docker container limits;
the Docker node may advertise more capacity than its container can actually use.
`up` reports resource warnings but still attempts deployment. Missing target,
architecture, storage, Secret, or ownership prerequisites stop the operation.

Every command requires `--profile`. Defaults are namespace `local-review-demo`
and release `local-review`. Override with `--namespace` and `--release`, and repeat
those options on subsequent commands. One release per namespace is supported.
The CLI uses an isolated temporary kubeconfig and never switches the global context.

## Initialize and deploy

```bash
scripts/minikube_demo.sh init --profile minikube
scripts/minikube_demo.sh doctor --profile minikube
scripts/minikube_demo.sh up --profile minikube
```

`init` creates the namespace and a signing Secret if missing. It validates and
reuses an existing key; it never rotates a key. It does not install the application.
You may instead create these prerequisites with ordinary kubectl commands from the
[Helm guide](helm-release.md#one-time-namespace-and-secret-preparation).
Private registry credentials remain an explicit external prerequisite.

`up` builds native backend/frontend images using the Docker layer cache, assigns
unique tags, verifies architecture and loaded CRI contents, and loads them into the
selected Minikube. It uses `pullPolicy: Never` for these local images and clears
any inherited backend/frontend digest. It does not deploy published release
images: use standard Helm and `release-values.yaml` for that workflow.

The deployment sequence is:

1. Validate configuration, target, ownership, external Secrets and storage.
2. Diagnose resources, build/load images, and render the Chart.
3. Save `values-local.json`, run Helm lint, and print an equivalent Helm command.
4. For an existing workload, pause frontend access, establish an idle SQLite write
   reservation, and stop the backend before changing the Helm release.
5. Run `helm upgrade --install --reset-values --wait` and check readiness.

The Chart's `initialize-users` init container initializes DynamoDB. No separate
host-side database initializer or Kustomize apply runs in this workflow.
On a failed operation, the operation journal reports failure; Helm resources and
status remain available for diagnosis. Pending Helm operations require inspection,
not automatic release-Secret deletion or a forced retry.

## Values and reproducibility

```bash
scripts/minikube_demo.sh init --profile minikube -f /path/to/site.yaml
scripts/minikube_demo.sh up --profile minikube -f /path/to/site.yaml --port 8081
```

Precedence is Chart defaults, `values-minikube.yaml`, user `-f` files in order,
explicit CLI options, and local build image identities. User values are not
silently imported from the previous release. Repeat your values/options on each
upgrade. `--port` updates the browser Origin; `--model-backend` and
`--storage-class` are optional explicit overrides. Deployment waits default to
3900 seconds (`--timeout`, also accepting the old `--cold-timeout` spelling).

The printed `values-local.json` is a complete ordinary values file. It contains
no generated secret material and can be passed directly to Helm:

```bash
helm upgrade --install local-review ./deploy/helm/local-review \
  --kube-context minikube --namespace local-review-demo \
  --reset-values -f /printed/path/values-local.json --wait --timeout 65m
```

Keep the matching Chart source/version with this file for reproducibility.
Diagnostic commands and `init` do not overwrite the last deployment values file.

## Status, access and acceptance

```bash
scripts/minikube_demo.sh status --profile minikube
scripts/minikube_demo.sh logs --profile minikube
scripts/minikube_demo.sh verify --profile minikube
scripts/minikube_demo.sh port-forward --profile minikube
```

`status` reads Helm and live resources, including releases installed or upgraded
with plain Helm. `port-forward` uses the port in the deployed Origin and binds
only loopback; it never reuses or kills an unrelated listener. Visit the printed
`http://localhost:PORT` URL, not `127.0.0.1`. Stop your foreground forwarding process
before running `verify`, which owns its own temporary forwarding process.

`verify` creates test accounts and a real review. It checks authentication, CSRF,
account isolation, model identity, and persisted results. By default it fences
idle workloads, restarts the local database/backend, checks that model-cache files
are unchanged, and verifies accounts, cookies and review history survived.
`--skip-restart` produces a partial result and exits nonzero, not a persistence pass.
No browser automation is implied; manual UI acceptance remains separate.

Acceptance records bind to cluster/namespace identity, Helm revision, computed
values and actual runtime image IDs. A manual Helm upgrade invalidates older
acceptance evidence without making the CLI dependent on a private deployment
record. A copied checkout can inspect the release without importing `owner.json`.

## Rollback

```bash
helm history local-review --kube-context minikube -n local-review-demo
scripts/minikube_demo.sh rollback --profile minikube --revision 1
```

Choose an existing revision explicitly. The CLI checks ownership, fences active
work, runs `helm rollback --wait`, and checks readiness. Required local images must
still be available. Rollback does not undo database writes, rotate external Secrets
or establish a new acceptance pass; run `verify` afterward.

## Undeploy and recovery

Default uninstall retains Chart PVCs, external Secrets and the namespace:

```bash
scripts/minikube_demo.sh undeploy --profile minikube
```

Reinstall with the same namespace/release and configuration to reuse retained,
Helm-owned PVCs. Ownership is checked before building; claims owned by another
release are never adopted. Explicit `persistence.*.existingClaim` references must
already exist and are treated as externally supplied data.

To also delete this deployment's data:

```bash
scripts/minikube_demo.sh undeploy --profile minikube \
  --purge-data --confirm-data-loss local-review-demo
```

Purge removes retained PVCs owned by this release and signing Secrets created by
`init`. User-supplied claims and Secrets remain outside purge, even when supplied
claims carry matching Helm annotations. It does not delete images, host Ollama,
or the Minikube cluster. Active/unmeasurable work blocks workload interruption.

To additionally remove a namespace that this CLI created:

```bash
scripts/minikube_demo.sh undeploy --profile minikube \
  --purge-data --delete-namespace --confirm-data-loss local-review-demo
```

The CLI inventories every namespaced resource type. Unknown resources or explicit
external claims prevent namespace deletion. Namespaces created independently must
be inspected/deleted separately. The confirmation value must match `--namespace`.

PVC deletion and storage reclamation are separate. The CLI waits for associated
PVs to disappear; `Retain` or a broken provisioner produces a `storage_pending`
report with remaining PV names and a nonzero exit. It never strips finalizers,
rewrites provisioner identity or removes node directories. Resolve the reported
storage issue and repeat the same cleanup command.

## State and legacy commands

Private records live under the configured state root's `helm-v1/` subtree, keyed
by cluster UID, profile/home, namespace and release. `--state-root` or
`LOCAL_QWEN_STATE_HOME` can override the default user state directory. Operations
are locally locked. Independent Helm commands should not run concurrently with a
CLI mutation or verification.

A namespace identity change archives stale Helm records. Old Kustomize ownership
records remain untouched in their original directories and are not adopted.
The legacy entry is explicit:

```bash
scripts/minikube_demo.sh legacy inspect-target --profile minikube
scripts/minikube_demo.sh legacy undeploy --profile minikube
```

Use these only for legacy resources, never for a Helm release. The older
`scripts/helm_deploy.py` is deprecated; it shares target/ownership helpers but is
not called by this local workflow. Standard Helm remains the published-release
entry point.

## Offline verification

```bash
scripts/check_minikube_demo.sh
backend/.venv/bin/python scripts/validate_helm.py
backend/.venv/bin/python -m pytest scripts/tests/test_helm_release.py -q
```

These checks use test doubles and local Helm rendering. They do not deploy, build
images, download models, or establish real-cluster acceptance. A live acceptance
pass must separately cover init, install, upgrade, direct Helm interoperability,
rollback, retained-data reinstallation and cleanup on a disposable namespace.
