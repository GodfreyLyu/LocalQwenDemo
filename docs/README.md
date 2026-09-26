# Documentation

LocalQwenDemo runs real CPU code review on an existing local minikube cluster. The
[project homepage](../README.md) introduces the capabilities, diagrams and shortest
start path. The operator manages cluster lifecycle; automation manages only verified,
owned application resources.

## Choose a reading path

| I want to…                             | Read                                                                                                | Purpose                                                                                      |
| -------------------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Try the application                    | [Quick start](../README.md#quick-start), then [minikube guide](guides/minikube-demo.md)             | Prepare a target, diagnose resources, deploy and open the same-origin UI                     |
| Understand the design                  | [Architecture](reference/architecture.md), [security](reference/security.md)                        | Follow jobs through one backend process and understand persistence and trust boundaries      |
| Develop or contribute                  | [Local development](guides/local-development.md), [testing](testing/README.md)                      | Set up tools, use fake-model or Compose development, and choose offline checks               |
| Move to another checkout               | [Shared state and import](guides/minikube-demo.md#state-and-ownership-protection)                   | Reuse trusted user-level state without treating paths or old images as ownership/build proof |
| Investigate a failure                  | [Troubleshooting](operations/recovery-and-cleanup.md), [observability](operations/observability.md) | Inspect safe evidence before changing workloads, retrying inference or deleting anything     |
| Remove an owned deployment             | [Undeploy](guides/minikube-demo.md#undeploy-and-recovery)                                           | Preserve data by default, or explicitly confirm a full owned-data purge                      |
| Recover when all trusted state is lost | [Lost-state recovery](operations/minikube-lost-state-recovery.md)                                   | Preview independently verified resources; cleanup requires deliberate data abandonment       |
| Validate results                       | [Testing and acceptance](testing/README.md), [model evaluation](testing/model-evaluation.md)        | Separate offline doubles, real reviews, persistence, full verify and browser acceptance      |
| Review previous observations           | [Dated reports](reports/README.md)                                                                  | Inspect original environment, date, outcome and limitations; not current acceptance proof    |

## Reference

| Contract                                                                            | Main reference                                                  |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Component boundaries, queue lifecycle and persistent data                           | [Architecture](reference/architecture.md)                       |
| Endpoints, request bodies, authentication and error responses                       | [API](reference/api.md)                                         |
| Pinned model, defaults versus minikube settings, cache validation and quality gates | [Model](reference/model.md)                                     |
| Cookie/CSRF, history isolation, local transport and container controls              | [Security](reference/security.md)                               |
| CLI options, dependencies, side effects and internal call chains                    | [Scripts](reference/scripts.md)                                 |
| Safe event fields and measurement semantics                                         | [Observability](reference/observability.md)                     |
| CPU/memory/disk budgets and availability limits                                     | [Resources and limitations](reference/costs-and-limitations.md) |

## How these documents fit together

- **README:** orientation, diagrams and a short first run. Detailed procedures live below.
- **`guides/`:** end-to-end setup and deployment workflows. The minikube guide owns profile
  selection, shared-state/import, deployment, acceptance and normal cleanup procedures.
- **`operations/`:** symptom-driven investigation and exceptional recovery. Lost-state
  cleanup has its own guide because its evidence and data-loss requirements differ from
  normal undeploy. The observability runbook applies the logging reference to investigations.
- **`reference/`:** implementation contracts, defaults and design explanations. Update the
  relevant reference when an API, configuration or behavior changes.
- **`testing/`:** check entry points, test-double boundaries, opt-in evaluation and the
  evidence required for each acceptance claim.
- **`reports/` and `evidence/`:** dated observations and original sanitized measurement
  artifacts. Preserve their outcomes and limitations; do not turn old results into current
  operating instructions or successful acceptance for new code.

Commands use the checkout root unless stated otherwise. `doctor` reads live diagnostics;
`up`, `verify`, cleanup and real-model evaluation have operational side effects. Offline
tests do not establish current cluster readiness, real-model quality or successful cleanup.

Current instructions are checked against repository source, manifests, CLI help and tests.
Historical cloud sections are archival only. Private state, credentials, user histories,
weights and runtime logs remain outside Git. DynamoDB Local retains SDK names but requires
explicit local endpoints and invalid credentials, with metadata/shared-configuration
fallback disabled.
