# LocalQwenDemo

PR-gated image builds and Argo CD: [GitOps guide](docs/guides/gitops.md).
Standard Helm deployment: [guide](docs/guides/helm-release.md).

GPU inference inside krunkit Minikube is available as an [independent Ollama Helm
release](deploy/helm/local-ollama/README.md), with a retained model PVC. The service
becomes ready only after GPU inference has been verified. The application defaults to
this release at `http://review-ollama.local-inference.svc.cluster.local:11434`.

**A local LLM code-review service with in-cluster Ollama inference and standard Helm deployment to an existing Minikube cluster.**

Paste a code snippet, receive a structured review, and revisit it in your private history.
The React UI, FastAPI backend and independent Ollama service run in Minikube. Ollama runs `qwen3:1.7b`
Q4_K_M at a pinned digest. The backend and evaluator use Ollama exclusively.
Submitted code is treated as text: it is never executed or sent to a cloud inference API.
Standard Helm deployment supports the existing krunkit GPU cluster. The optional
local image-build script requires a Docker-driver Minikube. Local development tools
are also available.

[Ollama acceptance](docs/reports/ollama-integration-2026-10-03.md) · [Quick start](#quick-start) · [Architecture](#architecture) · [Documentation](#documentation) · [Development](#development-and-contributing)

## Key capabilities

- **Durable submissions:** SQLite records jobs and enforces queue capacity in one
  transaction. It deduplicates matching request IDs for each account. Queued jobs
  survive restarts, and interrupted jobs have a limited number of retries.
- **One inference at a time:** a background coordinator uses one inference executor.
  After a timeout, the service waits for generation to stop before starting another
  review. It reports that it is not ready during this wait.
- **Private accounts and history:** password hashing, signed sessions, exact Origin and
  CSRF checks protect access. History is scoped to the authenticated account.
- **Verified model identity:** Startup verifies the selected Ollama model's digest, capability and
  context; saved reviews retain the actual model digest.
- **Reviewed Helm releases:** CI builds images and proposes immutable deployment
  snapshots; approved releases install with standard Helm commands and explicit
  environment values. Existing Secrets and persistent volumes survive upgrades.

Generated findings still need human review. Structural quality checks do not establish
semantic correctness, and CPU performance depends on the host. This is a local single-node
service, not a validated highly available or publicly exposed production platform.

## Architecture

### Application and persistent data

The backend's single executor calls
`review-ollama.local-inference.svc.cluster.local:11434`. Ollama owns a separate model
PVC and performs inference in the cluster. Ollama owns tokenization; the backend selects its model through configuration.
The [historical diagram](docs/assets/application-architecture.png) depicts the removed
in-process CPU implementation; see the [current request flow](docs/reference/architecture.md).

The browser reaches Nginx through a host loopback port-forward. Nginx serves the React
bundle and proxies `/api/` and `/health/` to FastAPI on the same origin.

Inside the **single backend process**, the API writes jobs to the SQLite queue. A background
coordinator claims one job, runs it in the single inference executor, and writes the outcome
back to SQLite. The submission request returns before inference; the browser polls
stored status. The coordinator, executor and SQLite access layer run within the backend.
The API and coordinator use that layer to access the same history PVC.

The application uses three separate PVCs. The backend uses the history and model-cache
PVCs; DynamoDB Local uses the accounts PVC. Ollama stores weights in its own model PVC;
the former backend tokenizer cache is retained but unused. Model weights are not baked into
application images. Existing cached HF weights are retained but no longer used.

### Deployment management

GitHub Actions builds application images and proposes a reviewed snapshot on
`deployment-release`. The snapshot contains the Chart, image digests and a
Minikube environment example. Deploy it with Helm; prepare the cluster, independent
Ollama release and namespace Secrets separately. No Python deployment wrapper is required.

The [deployment guide](docs/guides/helm-release.md) documents configuration,
installation, upgrades, status, rollback and data retention. Argo CD renders the same
Charts from the approved release branch. Each deployment must have only one manager.

The optional [local Helm CLI](docs/guides/minikube-demo.md) builds source images for
a Docker-driver Minikube. Argo CD deployments are managed through GitOps.

## Quick start

For local source development, the optional script builds/loads images and calls
standard Helm. Prepare an existing Docker-driver Minikube and a reachable Ollama
Service using the default namespace/release, then run:

```bash
scripts/minikube_demo.sh init --profile minikube
scripts/minikube_demo.sh up --profile minikube
scripts/minikube_demo.sh verify --profile minikube
scripts/minikube_demo.sh port-forward --profile minikube
```

See the [local Helm guide](docs/guides/minikube-demo.md) for configuration, rollback and
cleanup, with options to retain or delete data. Stop the foreground port-forward before
running `verify`. Helm commands remain usable independently of this script.

For published release snapshots:

1. Prepare an existing Minikube cluster (start with 4 CPUs / 8 GiB), its `standard`
   StorageClass, and the [independent Ollama release](deploy/helm/local-ollama/README.md)
   with the pinned model. The GPU image requires krunkit.
2. Check out an approved `deployment-release` snapshot. Follow the
   [one-time setup](docs/guides/helm-release.md#one-time-namespace-and-secret-preparation)
   to create a fresh namespace and stable signing Secret. Private GHCR images
   also require an image pull Secret.
3. Copy the provided Minikube values outside the checkout, adjust the environment,
   and follow the [standard Helm installation](docs/guides/helm-release.md#configure-inspect-and-install).
   Pass `release-values.yaml` before your environment file on every upgrade.
4. Port-forward `service/review-frontend`, open `http://localhost:8080`, and complete
   a real review. Kubernetes readiness alone is not model acceptance.

The local example disables NetworkPolicy isolation. The guide explains how to
enable policies with a capable CNI and select the in-cluster Ollama Pods. Source users
can run `helm lint` and `helm template` with
`deploy/helm/local-review/values-minikube.yaml` without a cluster; deploying source
also requires actual application images.

## Documentation

| Reader task                  | Start here                                                                                                                                 | What you will find                                                                    |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| Getting started              | [Helm deployment](docs/guides/helm-release.md)                                                                                             | Prerequisites, deployment, access and acceptance                                      |
| Architecture and design      | [Architecture](docs/reference/architecture.md), [security](docs/reference/security.md)                                                     | Request flow, queue lifecycle, storage and trust boundaries                           |
| Development and testing      | [Local development](docs/guides/local-development.md), [testing](docs/testing/README.md)                                                   | Setup, fake-model/Compose tools, offline gates and real acceptance boundaries         |
| Deployment and operations    | [Helm lifecycle](docs/guides/helm-release.md#status-uninstall-and-rollback), [observability runbook](docs/operations/observability.md) | Helm status, rollback, retained storage and diagnostics                       |
| Reference                    | [Documentation index](docs/README.md#reference)                                                                                            | API, model, CLI, logging and resource contracts                                       |
| Historical validation        | [Dated reports](docs/reports/README.md)                                                                                                    | Original environments, measured results and limitations; no current acceptance claims |

The [documentation index](docs/README.md) maps each task to its main guide or reference.
Project documentation and fixed output use English; user input and model
output retain their original language.

## Development and contributing

Follow the Python setup in [local development](docs/guides/local-development.md),
then install Node 24 dependencies and run the offline gate:

```bash
npm --prefix frontend ci
bash scripts/check.sh
```

The checks use test doubles for the model and cluster, temporary loopback fixtures, and
offline Helm rendering. They do not download weights, build container images or
deploy. Browser tests use a separate fake-model harness; real inference and environment
acceptance are opt-in operations. See [testing](docs/testing/README.md) for
prerequisites and coverage limits.

For a change, follow [local development](docs/guides/local-development.md), add focused
regressions, and update the owning reference or guide. Keep private state, credentials,
user data, weights and generated artifacts out of commits. Explain which checks actually
ran and preserve any skipped, failed or unmeasured result.
