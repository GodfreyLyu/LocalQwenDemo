# Local observability runbook

[Documentation index](../README.md) · [Safe log contract](../reference/observability.md)

For Docker-driver deployments managed by the local Helm CLI, start with
`scripts/minikube_demo.sh status --profile minikube` and
`scripts/minikube_demo.sh logs --profile minikube`. For standard Helm deployments,
use the explicit context and namespace commands in the
[recovery runbook](recovery-and-cleanup.md#health-and-startup). For Argo CD, inspect
Application sync/health and the selected Pod through the [GitOps guide](../guides/gitops.md).
Confirm the context and namespace before interpreting results. The optional monitoring
platform observes the application without automatically repairing it.

## Enable monitoring

Install the pinned platform through [Terraform monitoring setup](../../infra/local-platform/README.md#monitoring)
first. The `local-review-demo` namespace and external Grafana admin Secret must exist.
Wait for the Operator/CRDs, Prometheus and Grafana before enabling application resources.
Do not use Helm to mutate an Argo-managed deployment.

For standard Helm, append `deploy/helm/local-review/values-monitoring.yaml` after the
release and environment files on install and every upgrade. For Argo CD, the application
already lists that overlay last; merge its deployment snapshot only after platform
setup. The AppProject permits the two monitoring resource kinds in the application
namespace, while cluster-wide access remains prohibited.

Inspect discovery and reach the interfaces through loopback port-forwards:

```bash
kubectl --context minikube -n monitoring get pods,pvc
```

```bash
kubectl --context minikube -n local-review-demo get servicemonitor,prometheusrule
```

```bash
kubectl --context minikube -n monitoring port-forward service/monitoring-grafana 3000:80
```

Open `http://localhost:3000` with the external admin credentials. Use **Service Overview**
for API responses, review outcomes and queue state; use **Inference & Resources** for
section timings, observed token rates and separately scoped backend/Ollama resource
usage. API panels exclude health probes. Empty panels can mean no observations rather
than a failure or zero latency. Percentiles need enough samples to be useful.

For Prometheus and Alertmanager access, use the port-forward commands in the
[platform monitoring guide](../../infra/local-platform/README.md#monitoring).
Alertmanager displays and groups alerts locally; external notifications are not configured.

If Grafana probes report `connection refused`, inspect the `grafana` container's
last termination reason and previous logs. `OOMKilled` means the process exceeded its
container memory limit or encountered node memory pressure; extending probe delays
does not fix it. Check node capacity and adjust Grafana resources in the platform's
`values/monitoring.yaml`, then plan and apply with Terraform. Verify dashboard queries
and restart counts after the rollout. Pod replacement discards Grafana UI edits;
the provisioned dashboards and data sources are loaded again from ConfigMaps.

## Diagnose targets and alerts

Start with Prometheus Targets and
`up{namespace="local-review-demo",service="review-backend"}`. A value of zero means
a discovered target cannot be scraped; an absent series means no matching target
was discovered. Check the ServiceMonitor namespace/labels, backend Service selector
and named `http` port, endpoints, and NetworkPolicy before interpreting business metrics.
The expected monitor/rule label is `release: monitoring`.

| Alert | Investigation |
| --- | --- |
| `ReviewMetricsTargetDown`, `ReviewMetricsTargetMissing` | Target connectivity or discovery; missing-target allowance includes configured startup time |
| `ReviewCoordinatorUnavailable` | Safe startup events and operational state after the startup allowance |
| `ReviewInferenceStuck` | A stuck state was observed in the last five minutes; inspect current executor/Ollama state before recovery, since liveness may already have restarted the backend |
| `ReviewMetricsCollectionFailed` | SQLite readability and storage health; absent queue gauges do not mean zero |
| `ReviewQueueBacklog` | Oldest queued age exceeds capacity times the inference timeout for five minutes |
| `ReviewFailureRateHigh` | More than the configured failure ratio over 15 minutes, with a minimum completed-attempt count and five-minute hold |
| `ReviewContainerRestarts`, `ReviewContainerOOMKilled` | Pod events, termination reasons and container/node resource pressure |

Operational state `ready` alone does not prove writable storage or a successful review.
Use the existing readiness diagnostics and normal review acceptance separately. Review
counters reset on process restart; use `rate`/`increase` for time windows. The reference
defines original-submission versus per-attempt timings and missing-generation semantics.

The Kubernetes node is the Minikube VM, not the macOS host. Container/node CPU and
memory do not measure host swap pressure. Apple silicon/krunkit GPU utilization remains
`not_measured`; device allocation and model placement are not utilization counters.
Some hostpath volumes do not expose usage series, so missing PVC usage remains unknown.

## Validate configuration changes

The offline gate validates monitoring on/off, selectors, network boundaries and
dashboard JSON. With a local matching promtool binary, run:

```bash
backend/.venv/bin/python scripts/validate_monitoring.py --promtool /path/to/promtool
```

CI additionally downloads pinned tools, validates the two CRD schemas and checks
rendered alert behavior and dashboard PromQL. Runtime acceptance separately verifies
target `UP`, dashboard discovery and metrics from an authorized normal review. Offline
tests do not establish live scraping, CNI enforcement, resource fit or real inference.

The Helm CLI returns the backend container's last 100 log lines without further
filtering. The backend emits the structured events defined by the
[log contract](../reference/observability.md#safe-application-log-contract). Keep
diagnostic exports limited to those fields; exclude credentials and user content.

## Correlate before drawing conclusions

Confirm profile, cluster/namespace UID, Pod UID, container ID/start time, restart count
and running image before comparing logs or cgroup counters. Different container instances
have unrelated cumulative counters. Check startup stages and fixed errors, queue wait,
section generation/first-content durations and token counts. Ollama owns inference;
backend preparation/thread metrics are null.
First-token time is part of generation time, not an extra phase to add again.

Check host memory pressure and swap, node allocatable resources and workload
reservations, plus Docker limits for Docker-driver clusters. Where available, inspect
metrics-server usage and the container's `cpu.stat` and `memory.events` counters.
Record unavailable measurements as `not_measured`. Memory below a container limit and
zero sampled OOM events do not establish that the whole node was healthy.

## Controlled measurements

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
model storage or readiness. Do not automatically retry inference, raise timeouts, lower
quality requirements, restart Pods or resize resources in response to a metric alone.
Record the checks performed and remaining gaps in a separate dated, sanitized report.
