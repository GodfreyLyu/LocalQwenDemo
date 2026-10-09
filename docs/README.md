# Documentation

LocalQwenDemo runs code review using Ollama for both production and evaluation. The
[project homepage](../README.md) provides an overview and a quick start. The
operator manages the cluster lifecycle and external services. Standard Helm commands
manage application resources using reviewed release values and environment settings.

For GPU inference inside an existing krunkit cluster, see the
[independent Ollama Helm deployment](../deploy/helm/local-ollama/README.md).

## Choose a reading path

| I want to…                             | Read                                                                                                | Purpose                                                                                      |
| -------------------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Try the application                    | [Local Helm workflow](guides/minikube-demo.md), [release Helm guide](guides/helm-release.md)             | Prepare Secrets and environment values, install with Helm, and open the UI                     |
| Understand the design                  | [Architecture](reference/architecture.md), [security](reference/security.md)                        | Follow jobs through one backend process and understand persistence and trust boundaries      |
| Develop or contribute                  | [Local development](guides/local-development.md), [testing](testing/README.md)                      | Set up tools, use fake-model or Compose development, and choose offline checks               |
| Investigate a failure                  | [Troubleshooting](operations/recovery-and-cleanup.md), [observability](operations/observability.md) | Inspect safe evidence before changing workloads, retrying inference or deleting anything     |
| Remove a Helm deployment              | [Helm lifecycle](guides/helm-release.md#status-uninstall-and-rollback)                                           | Uninstall with Helm while retaining PVCs and the external signing Secret                      |
| Validate results                       | [Testing and acceptance](testing/README.md), [model evaluation](testing/model-evaluation.md)        | Separate offline doubles, real reviews, persistence, full verify and browser acceptance      |
| Review previous observations           | [Dated reports](reports/README.md)                                                                  | Inspect original environment, date, outcome and limitations; not current acceptance proof    |

## Reference

| Contract                                                                            | Main reference                                                  |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Component boundaries, queue lifecycle and persistent data                           | [Architecture](reference/architecture.md)                       |
| Endpoints, request bodies, authentication and error responses                       | [API](reference/api.md)                                         |
| Model identity, runtime and deployment defaults, startup checks and quality rules | [Model](reference/model.md)                                     |
| Cookie/CSRF, history isolation, local transport and container controls              | [Security](reference/security.md)                               |
| Manual and automatic entry points, options, dependencies and call chains | [Script entry points](../scripts/README.md), [script reference](reference/scripts.md) |
| Safe event fields and measurement semantics                                         | [Observability](reference/observability.md)                     |
| CPU/memory/disk budgets and availability limits                                     | [Resources and limitations](reference/costs-and-limitations.md) |

## How these documents fit together

- **README:** overview and quick start.
- **`guides/`:** setup and deployment workflows. The Helm guide covers standard
  installation, environment values and release operations. The Minikube guide
  covers the local Helm CLI.
- **`operations/`:** symptom-driven investigation, Helm/GitOps recovery and storage
  retention. The observability runbook applies the logging reference to investigations.
- **`reference/`:** behavior, defaults and design explanations. Update the
  relevant reference when an API, configuration or behavior changes.
- **`testing/`:** check entry points, test-double boundaries, opt-in evaluation and the
  evidence required for each acceptance claim.
- **`reports/` and `evidence/`:** dated observations and original sanitized measurements.
  Preserve their outcomes and limitations. Old results do not validate new code or
  replace current operating instructions.

Run commands from the checkout root unless stated otherwise. `doctor` reads live
diagnostics. Commands such as `up`, `verify`, cleanup and real-model evaluation can
change resources or data. Offline tests do not verify the current cluster, real-model
quality or live cleanup.

Repository source, manifests, CLI help and tests define current behavior.
Retired cloud deployment records remain in Git history. Private state, credentials,
user histories, weights and runtime logs remain outside Git. DynamoDB Local retains SDK names but requires
explicit local endpoints and dummy credentials. SDK metadata and shared-configuration
fallback are disabled.
