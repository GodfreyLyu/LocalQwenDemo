# Helm releases and GitHub Actions

`main` owns application source, Dockerfiles, `deploy/helm/local-review`, deployment
tools and workflows. `deployment-release` contains reviewed deployment snapshots.
Do not merge main into deployment-release or edit generated values there: change
the source Chart in main and review the generated release PR.

## Automated release flow

Every push to main calls the existing source quality workflow, fingerprints the
actual component inputs, builds changed images for linux/amd64 and linux/arm64,
pushes them to GHCR, renders and validates a deployment snapshot, then opens or
updates `automation/deployment-release` → `deployment-release`.

The initial release builds both images. Later releases retain each unchanged
image's digest and original source SHA. Configuration-only updates reuse images;
documentation outside the published deployment inputs produces no empty PR.
Build fingerprints compare with the open candidate, or the approved release when
there is no open candidate, so failed/cancelled intermediate runs do not lose changes.
Source quality runs even when there is no deployable difference.

The candidate branch is based on deployment-release and receives only the Chart,
release values, provenance, deployment tools and this guide. It never contains the
frontend/backend source trees. One PR accumulates changes until reviewed. The bot
uses a branch lease and checks both main and the release base before publishing.
An older run cannot replace a newer candidate. If the release base changes during
a build, rerun the workflow on the latest main.

`release.json` records the main SHA, input fingerprints, Chart version and each
image's source SHA/digest. The Chart uses a deterministic `-sha.<commit>` SemVer
prerelease. Merging the PR approves a snapshot; it does not deploy a cluster.

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

Recommended branch protection for deployment-release: require a reviewed PR and
the `deployment/snapshot` commit status. The release workflow calls validation
directly after publishing and posts that status to the exact candidate SHA. This
does not depend on a GITHUB_TOKEN-generated PR starting another workflow. The
release-branch PR entrypoint also validates same-repository manual updates.
The validator executes trusted main code and treats the candidate as data.

To retry a failed build, rerun **Release candidate** on current main. The manual
`rebuild` option republishes both images, for example after registry cleanup or
when intentionally refreshing base images. To retry candidate validation alone,
dispatch **Deployment validation** on main with the full candidate SHA. A normal
retry with an unchanged open candidate also invokes validation again.

## Deploy a reviewed snapshot to an explicit Minikube profile

Requirements: Python 3.12+, PyYAML 6, Helm 3.17+ or Helm 4, kubectl, Docker and an
already running single-node Minikube using the Docker driver. The tools do not
start/stop/delete clusters or switch global Kubernetes context.

From a separate checkout of deployment-release:

```bash
python3 -m venv /tmp/local-review-deploy-venv
/tmp/local-review-deploy-venv/bin/pip install pyyaml==6.0.3
/tmp/local-review-deploy-venv/bin/python scripts/helm_deploy.py install \
  --profile minikube --namespace local-review-demo --release local-review
/tmp/local-review-deploy-venv/bin/python scripts/helm_deploy.py port-forward \
  --profile minikube --namespace local-review-demo --release local-review
```

Open http://localhost:8080. Use `--port 8081` on both commands for another local
port. The tool checks profile/node/certificate/API identity and prints the cluster
UID. It generates a private temporary kubeconfig and passes context/namespace on
every Helm/kubectl command. It discovers the selected Docker node's Ollama host IP
and injects a narrow `/32` egress rule; the machine-specific IP is not committed.

The host must already run Ollama with the pinned qwen3:1.7b model accessible from
Minikube. Ollama weights are not bundled into these application images. The model
cache is still used for tokenizer validation. Transformers mode downloads/loads
the pinned model and may take substantially longer. Both modes can need outbound
HTTPS. NetworkPolicy requires an enforcing CNI; Helm cannot provide enforcement.

The Chart keeps fixed Service names, so use one release per namespace. Different
clusters or namespaces can run independent releases. Backend replicas are fixed
at one because SQLite queue recovery and inference ownership are not distributed.
Updates use Recreate and briefly interrupt availability; active work may be
recovered under the existing bounded retry policy. Prefer upgrading when idle.

### Configuration and secrets

Defaults live in main's `deploy/helm/local-review/values.yaml`. Snapshot-specific
image pins are in `release-values.yaml`, automatically loaded by the deployment
tool. Supply site overrides with `-f /path/to/site-values.yaml`:

```yaml
persistence:
  storageClass: standard
imagePullSecrets:
  - name: ghcr-pull
```

The startup budget defaults to 3600 seconds for cold model startup. The Helm wait
budget can be set with `--timeout`. Runtime values are validated against
`values.schema.json`; configuration checksum annotations roll Pods when values
change. Service names and the local DynamoDB endpoint deliberately remain fixed.

The wrapper creates a random signing Secret once if missing and validates existing
keys without printing them. It never rotates a key on upgrade. The Chart only
references `signingSecret.existingSecret`; direct Helm users must create it first.
Never commit credentials, kubeconfigs, signing keys or registry tokens. Configure
Secret references rather than plaintext secret values in Chart values.

For direct Helm use, explicitly pass `--kubeconfig`, `--kube-context`, `--namespace`,
`-f release-values.yaml` and an environment file containing the current Ollama
host `/32`. The wrapper is recommended because it verifies the target and discovers
that address. It does not require backend Python dependencies or source code.

## Existing Kustomize deployment migration

The legacy Kustomize scripts remain available during transition. They must never
manage workloads already transferred to Helm. The Helm tool refuses automatic
takeover of existing non-Helm resources.

1. Record the old profile, namespace, ownership state and signing Secret; take
   consistent backups of SQLite and DynamoDB while workloads are stopped. A PVC
   is persistence, not a backup.
2. Use the existing owned `minikube_demo.py undeploy --profile NAME` flow without
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
python scripts/helm_deploy.py status --profile minikube
python scripts/helm_deploy.py uninstall --profile minikube
```

Uninstall removes Helm-managed application resources but keeps Chart-created PVCs
using `helm.sh/resource-policy: keep`. Externally managed claims and the signing
Secret are not part of the release. The namespace is retained. A retained claim
is not automatically portable across namespaces or clusters, and StorageClass
changes do not migrate existing data.

To roll back, open a PR restoring the desired earlier Chart, release-values and
release.json together, validate it, merge and explicitly deploy it. Reuse retained
PVCs/Secret. Helm or Git rollback does not undo database changes. If main has moved
on, close or review an existing automatic candidate so it does not accidentally
re-promote the version you just rolled back.

## Verification

```bash
python scripts/validate_helm.py --snapshot
```

Source CI also checks source Chart variants, configuration rollouts, PVC retention,
single-worker constraints, cumulative changes, image reuse/provenance, malformed
digests and explicit target selection. Offline checks do not prove real model
inference, multi-architecture image execution or the behavior of a particular CNI;
those are separate environment acceptance checks.
