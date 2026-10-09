# Local observability runbook

[Documentation index](../README.md) · [Safe log contract](../reference/observability.md)

For Docker-driver deployments managed by the local Helm CLI, start with
`scripts/minikube_demo.sh status --profile minikube` and
`scripts/minikube_demo.sh logs --profile minikube`. For standard Helm deployments,
use the explicit context and namespace commands in the
[recovery runbook](recovery-and-cleanup.md#health-and-startup). For Argo CD, inspect
Application sync/health and the selected Pod through the [GitOps guide](../guides/gitops.md).
Confirm the context and namespace before interpreting results. No external monitoring
service or automated repair is installed by this project.

The Helm CLI returns the backend container's last 100 log lines; it does not apply a
second field filter. The backend emits the structured safe events defined by the
[log contract](../reference/observability.md). Inspect only the needed events and fields;
do not export environment dumps, credentials, source text or unrelated raw output.

## Correlate before drawing conclusions

Confirm profile, cluster/namespace UID, Pod UID, container ID/start time, restart count
and running image before comparing logs or cgroup counters. Different container instances
have unrelated cumulative counters. Check startup stages and fixed errors, queue wait,
section generation/first-content durations and token counts. Ollama owns inference;
backend preparation/thread metrics are null.
First-token time is part of generation time, not an extra phase to add again.

Check available measurements of host memory pressure and swap, Docker limits, node
allocatable resources and workload reservations. Also inspect metrics-server usage and
the container's `cpu.stat` and `memory.events` counters. Unavailable measurements must
stay `not_measured`; do not install collectors or change infrastructure to produce a
pass. Memory below a limit and zero sampled OOM events do not prove the whole node was
healthy. Backend cgroup counters do not measure the independent Ollama process.

## Controlled measurements are separate work

A real review changes history and uses Ollama's CPU, GPU, and memory resources. Obtain authorization,
confirm Ready, an empty queue and no draining inference, and submit once through normal
authentication. Collect before/after counters from the same container, including the
cancellation/drain interval. Calculate average cores from usage delta divided by wall
time and CPU seconds/token from the measured token count. Do not translate cumulative
throttled fractions into wall-time loss. Stop at `inference_stuck`; do not run
overlapping model processes.

Keep parameters, workload identity and environment differences explicit. A completed
review must still pass quality checks; one sample does not establish repeatability or
full verify/browser acceptance. Preserve failures and partial generation metrics without
recording source, prompts, output text, passwords, Cookies or tokens.

## Next action

Use [startup/recovery troubleshooting](recovery-and-cleanup.md) for failed dependencies,
cache, storage or readiness. Do not automatically retry inference, raise timeouts, lower
quality requirements, restart Pods or resize resources in response to a metric alone.
Record the checks performed and remaining gaps in a separate dated, sanitized report.
