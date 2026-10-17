# Documentation

LocalQwenDemo uses Ollama for code review and model evaluation. Start with the
[project overview](../README.md) if you are new to the application, or choose a guide
below for your task.

## Choose a reading path

| I want to… | Read |
| --- | --- |
| Set up a krunkit GPU cluster | [Local platform](../infra/local-platform/README.md) |
| Deploy reviewed releases with Argo CD | [GitOps guide](guides/gitops.md) |
| Install a release with Helm | [Helm release guide](guides/helm-release.md) |
| Deploy the independent inference service | [Ollama Chart](../deploy/helm/local-ollama/README.md) |
| Build source images on Docker-driver Minikube | [Local Helm workflow](guides/minikube-demo.md) |
| Develop the application | [Local development](guides/local-development.md) |
| Run checks or evaluate the model | [Testing](testing/README.md), [model evaluation](testing/model-evaluation.md) |
| Investigate a failure or remove a deployment | [Recovery and cleanup](operations/recovery-and-cleanup.md) |
| Use dashboards, alerts and logs | [Observability runbook](operations/observability.md) |
| Review earlier measurements | [Dated reports](reports/README.md) |

## Reference

| Topic | Main reference |
| --- | --- |
| Components, request flow, queue lifecycle and storage | [Architecture](reference/architecture.md) |
| Endpoints, authentication and error responses | [API](reference/api.md) |
| Model identity, settings, startup checks and output checks | [Model](reference/model.md) |
| Sessions, CSRF, history isolation and container controls | [Security](reference/security.md) |
| Script entry points, options and dependencies | [Script guide](../scripts/README.md) |
| Metrics, log fields and measurement limits | [Observability](reference/observability.md) |
| CPU, memory, disk and availability limits | [Resources and limitations](reference/costs-and-limitations.md) |

## Keeping documentation current

The root README provides an overview. Guides explain setup and deployment;
operations pages cover investigation and recovery; references describe behavior and
settings. Testing pages explain what each check verifies. Keep detailed instructions
in the relevant page and link to it from other documents.

Reports and evidence files preserve dated observations. Their results apply to the
recorded revision and environment, so retain their outcomes and limitations when
updating current instructions. Retired deployment procedures remain in Git history.

Run commands from the checkout root unless stated otherwise. Some commands, including
`up`, `verify`, cleanup and real-model evaluation, change resources or data; their
individual guides explain the effects. Offline checks do not validate the running
cluster or real-model output.

Use source, manifests, CLI help and tests to check documented behavior. Keep private
state, credentials, user histories, weights and runtime logs outside Git.
