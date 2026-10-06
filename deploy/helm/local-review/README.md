# Local Review Helm Chart

Deploy with standard Helm commands. No project-specific deployment wrapper,
Python installation, host discovery, or cluster lookup is required to render or
install the Chart. See the [deployment guide](../../../docs/guides/helm-release.md)
for namespace/Secret preparation and the complete installation procedure.

## Prerequisites

- An existing Kubernetes >=1.30 cluster and usable StorageClass; the local example
  uses Minikube's `standard` class. Start with 4 CPUs / 8 GiB for the cluster.
- The [independent GPU Ollama release](../local-ollama/README.md), installed as
  `review-ollama` in namespace `local-inference`, with the pinned model. Both default
  values and `values-minikube.yaml` use its Service on port 11434. The optional
  `values-minikube-ollama.yaml` overlay reduces backend resource requests.
- An existing Secret in the release namespace, named by
  `signingSecret.existingSecret`, containing `SIGNING_SECRET` (at least 32 characters).
  Preserve it across upgrades and reinstallation.
- Registry pull credentials in `imagePullSecrets` when using private images.

The backend init container initializes the DynamoDB users table. Readiness probes
check application startup; completing a real review remains a separate check.

## Configuration and installation

From an approved `deployment-release` checkout, after preparing the prerequisites:

```bash
helm upgrade --install local-review ./deploy/helm/local-review \
  --kube-context tf-lab \
  --namespace local-review-helm --create-namespace \
  -f release-values.yaml \
  -f deploy/helm/local-review/values-minikube.yaml \
  --wait --timeout 65m
```

Replace the context with your target and use a new namespace for initial testing.
For custom environment settings, copy `values-minikube.yaml` outside the checkout
and pass that path as the last `-f` argument. Keep using the same file on upgrades.
Release values pin the published images; the environment file supplies local
settings. Helm does not automatically load either file. Main's default image
names require local image loading or explicit image overrides before installation.

The Minikube example **disables NetworkPolicy isolation**. To enable policies,
use an enforcing CNI and set `networkPolicy.enabled: true`. Defaults select the
`review-ollama` Pods in `local-inference` for egress on port 11434. When changing
release/namespace, update both `model.ollamaBaseUrl` and the policy selectors.

For explicit host Ollama, set `model.ollamaBaseUrl` to
`http://host.minikube.internal:11434`, clear `networkPolicy.ollamaNamespace`, and
set `networkPolicy.ollamaHostCidr` to the actual private host IPv4 `/32` if policies
are enabled. Host discovery is not part of standard Helm deployment.

Render and lint from main without credentials or cluster access:

```bash
helm lint ./deploy/helm/local-review -f deploy/helm/local-review/values-minikube.yaml
helm template local-review ./deploy/helm/local-review \
  --namespace local-review-helm -f deploy/helm/local-review/values-minikube.yaml
```

## Lifecycle

One release per namespace; fixed Service names preserve local endpoint validation.
Backend replicas remain one and updates use `Recreate`. Chart-created PVCs are
retained on Helm uninstall. To reuse storage, set the three
`persistence.*.existingClaim` values and retain the original signing Secret.

The Chart has deterministic templates and can also be rendered by Argo CD. Choose
one manager for each deployment; Argo CD instances are not Helm releases.
`scripts/helm_deploy.py` is retained only as a legacy alternative.
