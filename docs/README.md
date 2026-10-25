# Documentation

LocalQwenDemo reviews code through a FastAPI backend, an Apache Thrift worker and
local Ollama inference. Start with the
[project overview](../README.md) if you are new to the application, or choose a guide
below for your task.

## Choose a reading path

| I want to… | Read |
| --- | --- |
| Understand or present the complete system | [Architecture diagrams](reference/architecture.md#system-layout), [workflow diagrams](reference/workflows.md) |
| Learn how Apache Thrift connects the backend and worker | [Thrift walkthrough and diagrams](guides/apache-thrift.md) |
| Use the LLM service from another Java application | [Java SDK, Maven setup and consumer example](../clients/java/README.md) |
| Set up a krunkit GPU cluster | [Local platform](../infra/local-platform/README.md) |
| Deploy reviewed releases with Argo CD | [GitOps guide](guides/gitops.md) |
| Track a release, roll back and fix main | [Release tags and rollback](guides/release-rollback.md) |
| Install a release with Helm | [Helm release guide](guides/helm-release.md) |
| Deploy the independent Ollama model service | [Ollama Chart](../deploy/helm/local-ollama/README.md) |
| Build source images on Docker-driver Minikube | [Local Helm workflow](guides/minikube-demo.md) |
| Access this project's services through background forwards | [Minikube endpoints](../scripts/README.md#background-minikube-endpoints) |
| Develop the application | [Local development](guides/local-development.md) |
| Run checks or evaluate the model | [Testing](testing/README.md), [model evaluation](testing/model-evaluation.md) |
| Investigate a failure or remove a deployment | [Recovery and cleanup](operations/recovery-and-cleanup.md) |
| Use dashboards, alerts and logs | [Observability runbook](operations/observability.md) |
| Review earlier measurements | [Dated reports](reports/README.md) |

## Reference

| Topic | Main reference |
| --- | --- |
| Components, request flow, queue lifecycle and storage | [Architecture](reference/architecture.md) |
| Thrift IDL, generated code, handshake and cancellation | [Thrift walkthrough](guides/apache-thrift.md) |
| Java library API, connection settings and Maven dependencies | [Java SDK](../clients/java/README.md) |
| Authentication, review execution, recovery and release diagrams | [Workflows](reference/workflows.md) |
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

Architecture and workflow diagrams use editable Mermaid fences that render on GitHub.
Keep them aligned with the linked source when changing behavior; see
[diagram maintenance](reference/workflows.md#maintaining-the-diagrams).

Reports and evidence files preserve dated observations. Their results apply to the
recorded revision and environment, so retain their outcomes and limitations when
updating current instructions. Retired deployment procedures remain in Git history.

Run commands from the checkout root unless stated otherwise. Some commands, including
`up`, `verify`, cleanup and real-model evaluation, change resources or data; their
individual guides explain the effects. Offline checks do not validate the running
cluster or real-model output.

Use source, manifests, CLI help and tests to check documented behavior. Keep private
state, credentials, user histories, weights and runtime logs outside Git.

The [generic Thrift guide](guides/apache-thrift.md) explains both application projects,
Python/Java clients and execution guarantees. Review the [inference migration and
rollback procedure](operations/inference-migration.md) before deploying the refactor.

The [generic service verification record](testing/generic-llm-verification-2026-10-25.md)
distinguishes real host inference, model doubles, image checks and remaining live
Vulkan deployment verification.
