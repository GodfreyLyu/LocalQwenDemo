# Local observability runbook

[Documentation index](../README.md) · [Safe log contract](../reference/observability.md)

Start with `scripts/minikube_demo.sh logs --profile minikube` and `status` using trusted
shared state. If identity evidence is missing, use the [recovery inspection path](minikube-lost-state-recovery.md)
without authorizing deletion. Do not use raw logs/environment dumps as a debugging shortcut.
No external monitoring service or automated repair is installed by this project.

## Correlate before drawing conclusions

Confirm profile, cluster/namespace UID, Pod UID, container ID/start time, restart count
and running image before comparing logs or cgroup counters. Different container instances
have unrelated cumulative counters. Check startup stages and fixed errors, queue wait,
section preparation/generation/first-token durations, token counts, and actual threads.
First-token time is part of generation time, not an extra phase to add again.

Read available host pressure/swap, Docker limits, node allocatable/workload reservations,
metrics-server usage and container cpu.stat/memory.events. Unavailable measurements must
stay `not_measured`; do not install collectors or change infrastructure to produce a pass.
Memory below a limit and zero sampled OOM events do not prove the whole node was healthy.
A configured BF16 dtype or architecture name does not establish acceleration.

## Controlled measurements are separate work

A real review changes history and consumes substantial CPU/RAM. Obtain authorization,
confirm Ready, an empty queue and no draining inference, and submit once through normal
authentication. Collect before/after counters from the same container, including the
cancellation/drain interval. Calculate average cores from usage delta divided by wall
time and CPU seconds/token from the measured token count. Do not translate cumulative
throttled fractions into wall-time loss. Stop at `inference_stuck`; do not overlap models.

Keep parameters, workload identity and environment differences explicit. A completed
review must still pass quality checks; one sample does not establish repeatability or
full verify/browser acceptance. Preserve failures and partial generation metrics without
recording source, prompts, output text, passwords, Cookies or tokens.

## Next action

Use [startup/recovery troubleshooting](recovery-and-cleanup.md) for failed dependencies,
cache, storage or readiness. Do not automatically retry inference, raise timeouts, lower
quality requirements, restart Pods or resize resources in response to a metric alone.
Publish a separate dated sanitized report with exact checks performed and remaining gaps.
