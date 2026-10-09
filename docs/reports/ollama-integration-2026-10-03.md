# Ollama integration acceptance — 2026-10-03

> Historical snapshot: the Transformers option described below has since been removed.
> Current runtime and evaluation both use [Ollama](../reference/model.md).

This is an integration acceptance record, not a controlled performance comparison.
The application runs in the existing native arm64 minikube cluster; inference uses
host Ollama 0.35.1. The previous Transformers CPU path remains explicitly selectable.

## Configuration

- Ollama model: `qwen3:1.7b`, Q4_K_M.
- Manifest digest: `sha256:8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7`.
- Device: GPU, as observed via Ollama `/api/ps` at inference time.
- Three independent sections, 2048 maximum input tokens per complete prompt,
  384 total output-token budget, concurrency 1, 300-second whole-job timeout.
- Actual model digest is returned by runtime and stored with completed reviews.
- Host egress is generated at deployment time; this deployment resolved
  `host.minikube.internal` to `192.168.65.254` and allowed only TCP 11434 to that /32.

## Observed results

| Check | Result |
| --- | --- |
| Deployment | Passed; backend ready in 11.0 seconds after rollout wait began |
| API review, fixed `average(values)` sample | Completed; 4.9 seconds including polling |
| Authentication, CSRF rejection, account isolation | Passed |
| History, re-login, controlled Pod recreation | Passed; recreation 30.6 seconds |
| Active adapter cache and model identity after recreation | Unchanged |
| Browser submission, visible three sections and model provenance | Passed; 3.356 seconds to visible Summary |
| Browser refresh and re-login history | Passed |
| Adapter connection refused | Explicit `ollama_unavailable`, 0.021 seconds |
| Adapter missing model | Explicit `ollama_model_missing`, 0.023 seconds |
| Adapter 50 ms deadline | `inference_timeout`, returned in 0.052 seconds |
| Adapter cancellation event at 80 ms | `inference_timeout`, returned in 0.125 seconds |
| Real review immediately after these adapter failures | Completed in 2.343 seconds |
| Transformers fallback startup in a separate Pod process | Cached BF16 weights loaded and one token generated; 19.48 seconds |

Fault probes changed only their own process settings and left the running application's
configuration unchanged. They confirmed that the asynchronous transport returned after
each failure and that a later request completed. Coordinator queue recovery and serial
execution also have offline tests. The fallback smoke does not constitute a new full CPU
review benchmark.

The first deployment exposed transient connectivity during new Pod startup. The
adapter now retries startup availability checks for at most 60 seconds, with bounded
backoff and individual operation deadlines. Missing model, digest/template mismatch
and malformed metadata fail without retry. It never switches adapters automatically.

## Known limits

- **Network isolation is not verified.** The prohibited backend-to-frontend probe was
  reachable. CNI discovery reported `portmap`/`ptp` with unknown policy support.
  Existing cluster network components were not changed. Do not present isolation as
  passed.
- Resource diagnostics reported host/node memory pressure, a conservative disk-budget
  shortfall, and unavailable Pod metrics. Application acceptance passed independently.
  CPU fallback resource reservations and image dependencies were retained.
- Backend cgroup memory at API acceptance was 294,006,784 bytes (peak 304,218,112). This
  excludes native host Ollama/GPU memory and cannot be used as total system memory.
- These timings come from single short samples measured at different points. They do not
  establish p50/p95 latency, throughput, cold-start performance or a reliable speedup
  over historical CPU runs.
- Existing quality gates passed; human review of generated findings is still needed.

## Original reproduction procedure

These commands and paths belong to the recorded revision. The `--model-backend` flag
and legacy verification module have since been removed. For the current deployment,
follow the [Helm guide](../guides/helm-release.md) and
[model reference](../reference/model.md).

Start native Ollama with the pinned model and a bind address reachable from minikube.
From the repository root:

```bash
scripts/minikube_demo.sh up --profile minikube --model-backend ollama
scripts/minikube_demo.sh verify --profile minikube
scripts/minikube_demo.sh port-forward --profile minikube
```

At `http://localhost:8080`, create a test account, select Python, submit the fixed
`average(values)` sample from `scripts/minikube_verify.py`, inspect the model digest,
refresh history, sign out and sign back in. The CLI report intentionally retains
`ui_verified=false`; browser evidence is a separate check, not a rewritten API report.
Use `up --model-backend transformers` for a separately measured CPU baseline.

For a future comparison, run both adapters against a fixed dataset with matching
budgets. Record cold and warm runs, latency percentiles, output tokens, quality results
and total host resource usage. Resolve and re-test the observed network isolation
failure separately.
