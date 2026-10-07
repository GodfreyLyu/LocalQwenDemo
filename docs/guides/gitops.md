# PR-gated images and Argo CD

Source changes enter `main` only through PRs. `Quality checks` runs on PRs and the
required `main-ci` gate succeeds only if both application and configuration jobs
succeed. Main protection requires an up-to-date branch and resolved conversations;
administrators cannot bypass these checks. This personal project requires no review
approvals, so authors can merge their own PRs after CI passes. GitHub does not support
self-approval. The `deployment-release` branch also requires no approvals. It still
requires the `deployment/snapshot` check and an up-to-date PR, and administrators must
follow the same rules.

After merge, `Release candidate` rechecks the exact main commit, builds changed
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

## One-time repository migration

1. Merge the implementation PR to main after `main-ci` succeeds.
2. Using a maintainer's GitHub CLI identity with workflow permissions, run:

   ```bash
   python3 scripts/release/migrate.py
   ```

   Review and merge this separate PR to deployment-release. It removes exactly
   `.github/workflows/deployment-validation.yml`; it does not alter images or workload
   configuration. The trusted validator on main checks that the PR uses the current
   base, has a single parent and deletes only that file. It then reports the
   `deployment/snapshot` result. The ordinary Actions token cannot modify workflow
   files, so this step cannot be hidden in image publication. Existing migration PRs are
   reused, never force-pushed.
3. Run **Release candidate** on main. The first main run may have stopped at this
   migration prerequisite; start a fresh run after the migration PR merges.
4. Review the three-image candidate and merge only after `deployment/snapshot`.

New repositories use `scripts/release/bootstrap.py`, which creates only a README on the
initial deployment branch. The generator removes legacy scripts and docs when generating
format-3 snapshots. Release files are limited to the two Helm Charts, `deploy/argocd`,
pinned values, `release.json` and a short README. The root `release-values.yaml` is
retained for existing Helm commands and is checked to be identical to local-review's
Chart-local `values-release.yaml`.

Apply repository protection using a maintainer identity:

```bash
gh api --method PUT repos/GodfreyLyu/LocalQwenDemo/branches/main/protection \
  --input deploy/release/main-protection.json
gh api --method PUT repos/GodfreyLyu/LocalQwenDemo/branches/deployment-release/protection \
  --input deploy/release/branch-protection.json
```

Keep the existing immutable candidate ruleset. Do not enable automatic deletion
of merged branches. Do not use administrator merges to bypass either PR gate.

## Argo CD installation and first deployment

Terraform owns the platform namespaces, device plugin and optional Argo CD Helm
release. Enable `argocd_enabled = true` in your ignored platform terraform.tfvars,
then run init, plan and apply as documented in the platform README. Argo CD uses
the pinned Chart 10.9.6, ClusterIP access and annotation-based resource tracking.
ApplicationSets, Dex and notifications are disabled for the local cluster.

The application namespaces and signing Secret must exist before bootstrap. Private GHCR
packages additionally require pull credentials in **both** application namespaces,
configured in each Chart's environment values. Access to a private Git repository
requires a separate Argo CD repository Secret. None of these credentials belongs in Git.
For public packages, no image pull Secret is needed. Verify GHCR package visibility
before the first sync; repository visibility alone does not set package visibility.

Use a separate checkout of the **approved format-3 deployment-release snapshot**.
For a fresh installation, create local-inference and local-review-demo namespaces
and prepare the stable review-secrets Secret as described in helm-release.md.
The limited AppProject intentionally cannot create cluster-scoped resources.

```bash
kubectl --context minikube apply -f deploy/argocd/project.yaml
kubectl --context minikube apply -f deploy/argocd/review-ollama-application.yaml
kubectl --context minikube -n argocd wait application/review-ollama \
  --for=jsonpath='{.status.operationState.phase}'=Succeeded --timeout=2400s
kubectl --context minikube -n argocd wait application/review-ollama \
  --for=jsonpath='{.status.health.status}'=Healthy --timeout=2400s
kubectl --context minikube apply -f deploy/argocd/local-review-application.yaml
kubectl --context minikube -n argocd wait application/local-review \
  --for=jsonpath='{.status.health.status}'=Healthy --timeout=2400s
```

Both apps automatically track deployment-release, prune obsolete workloads and self-heal
drift. Their names match Helm release names to preserve labels and the Ollama Service
URL. Follow the order shown above for the first deployment. Later updates to the two
Applications are independent and are not applied as one transaction. Backend readiness
checks, restarts and sync retries handle temporary Ollama unavailability.

After verifying the child Applications, run the following command from the main source
checkout:

```bash
kubectl --context minikube apply \
  -f infra/local-platform/kubernetes/argocd/root-application.yaml
```

This small root app watches only deploy/argocd so later approved Application
configuration updates also reconcile. It uses the bootstrap/default project,
does not prune child Applications, and has no cascading deletion finalizer.
Restrict write access to the release branch because this is a control-plane entry.
The child AppProject permits only the two application namespaces and required
workload kinds. Routine application sync cannot modify the GPU plugin or Argo CD.

## Verification, storage and ownership

The Ollama Application skips Helm test hooks and enables a separate `PostSync` Job. It
calls the Service to generate text and verify the model, digest and full GPU residency
using the runtime's existing checks. It does not request a second GPU slot. A failed Job
fails sync; readiness continuously checks the resident model. Standard Helm installs
still support `helm test` and do not render the Argo Job unless enabled explicitly. See
[Argo Helm hooks](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/).

All four PVCs have the Helm keep annotation and the Argo `Prune=false,Delete=false`
annotations. Deleting a namespace or cluster still deletes its storage; these
annotations are not backups. Bootstrap does not delete or recreate namespaces or signing
keys. Before applying an Application for an existing Helm-managed workload, record its
exact values, image digests and PVCs. Plan the end of Helm management and verify that
Argo renders matching resources before transferring ownership. Never let Helm and Argo
upgrade one workload concurrently, or blindly uninstall a release to perform migration.

Finish acceptance by submitting a real code review through the frontend and
checking saved history. GPU admission is not model-quality or load acceptance.
Roll back through a reviewed Git change restoring earlier digests/configuration;
with autosync enabled, an imperative Helm rollback is not the source of truth.
