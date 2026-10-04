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

For an audited rollback, revert the intended application/Chart/configuration
changes through a PR to main. CI then produces a new versioned candidate against
the current approved release; review, merge and explicitly deploy that snapshot.
Do not restore an old release.json verbatim: its version and release base belong
to an earlier candidate. Reuse retained PVCs/Secret. A rollback does not undo
database changes; verify compatibility before deploying earlier application code.

## Verification

```bash
python scripts/validate_helm.py --snapshot
```

Source CI also checks source Chart variants, configuration rollouts, PVC retention,
single-worker constraints, cumulative changes, image reuse/provenance, malformed
digests, immutable version/retry behavior, failed-candidate preservation, stale
base rejection, supersession and explicit target selection. Offline checks do not prove real model
inference, multi-architecture image execution or the behavior of a particular CNI;
those are separate environment acceptance checks.
