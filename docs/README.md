# Documentation index

LocalQwenDemo is a local CPU-based LLM code-review service with automated deployment
to an existing minikube cluster. Start with the [README](../README.md), then follow the
[minikube deployment guide](guides/minikube-demo.md). The user manages cluster lifecycle;
automation manages only explicitly owned application resources.

## Reading order

1. Read the minikube prerequisites, diagnostics, deployment and acceptance procedure.
2. Read architecture, model and security contracts before changing behavior.
3. Use the script catalog and offline testing guide before running automation.
4. For checkout moves, use shared state or trusted import. For cleanup, distinguish
   normal undeploy from explicitly confirmed lost-state data abandonment.
5. Consult dated reports only for their recorded observations and limitations.

## Guides

| Document | Purpose |
| --- | --- |
| [Minikube](guides/minikube-demo.md) | Default deployment path: existing native Docker-driver cluster, diagnostics, real model, acceptance, state import and cleanup |
| [Local development](guides/local-development.md) | Python/Node/IDE setup, fake-model harness, Compose accounts and optional real-model development |

## Reference

| Document | Purpose |
| --- | --- |
| [Architecture](reference/architecture.md) | Same-origin request flow, component boundaries, persistence and queue lifecycle |
| [API](reference/api.md) | Authentication, request and error contracts |
| [Model](reference/model.md) | Fixed model/revision, prompts, generation limits, cache and quality checks |
| [Security](reference/security.md) | Local credentials, Cookie/CSRF, user isolation and container boundaries |
| [Scripts](reference/scripts.md) | Maintained commands, exact options, side effects and call chains |
| [Observability](reference/observability.md) | Content-free structured logs and measurement boundaries |
| [Resources and limitations](reference/costs-and-limitations.md) | CPU/memory/disk budgets, unavailable metrics and single-node limitations |

## Operations

| Document | Purpose |
| --- | --- |
| [Recovery and cleanup](operations/recovery-and-cleanup.md) | Startup failures, persistent data, trusted ownership and normal cleanup |
| [Lost-state recovery](operations/minikube-lost-state-recovery.md) | Independently confirmed identities, complete inventory, queue fencing and explicit data abandonment |
| [Observability runbook](operations/observability.md) | Safe local diagnosis; no inference or restart implied by inspection |

## Testing

[Offline checks and acceptance](testing/README.md) distinguish fake-model tests,
application Ready, real completed reviews, persistence, full API verification and browser
acceptance. [Model evaluation](testing/model-evaluation.md) documents the opt-in, fixed
synthetic suite and content-free reporting; ordinary tests use doubles only.

## Reports

[Dated reports](reports/README.md) preserve local measurements and historical mixed
observations without converting them into current success. Historical cloud references
are archival context, not maintained commands. Original [JSON evidence](evidence/) remains
unchanged. Private state, credentials, local histories, caches and logs remain outside Git.

Commands use the repository root unless stated otherwise. `up`, `verify`, cleanup and
real-model evaluation have operational side effects; a command name alone does not imply
read-only behavior. DynamoDB Local requires boto3/SDK names but uses explicit local
endpoints, inert credentials and no metadata/shared-configuration fallback.
