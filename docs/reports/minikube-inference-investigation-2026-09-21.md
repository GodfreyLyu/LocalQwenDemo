# CPU inference timeout investigation — 2026-09-21

Historical report: results apply only to the dates, models and environments stated below. This document is not evidence that the current checkout was revalidated. Audience: maintainers investigating prior work; prerequisite: [current documentation index](../README.md).

Status: `investigated`; performance root cause: `not_determined`.
No inference, verification run, deployment, restart, resource adjustment, or data repair
was performed during this investigation. The diagnostics change described below is
local only and has **not** been deployed. Application readiness is not inference
acceptance or browser acceptance.

## Identity and timeline

Read-only discovery selected the existing `minikube` profile and used a temporary
private kubeconfig with explicit context and namespace `local-review-demo`.
Namespace, backend Deployment/ReplicaSet ownership, and the three PVC ownership
markers were checked. No Secret contents or review source/result bodies were read.

- Cluster UID: `87a4de40-9556-4415-bd6c-551ce09b2f92`.
- Namespace UID: `9f452678-fb0f-4088-be6d-6bdb201bcf67`.
- Pod: `review-backend-69f6cbdb8-cz5z2`.
- Pod UID: `abf60107-4024-4030-913c-432d60a93554`.
- Container ID: `83ca2d63fde0ce39011c5b9007f92d595cfe59e8a766fbcff210b62e16b2c7de`.
- Container started: `2026-09-21T07:31:16Z`; restart count: `0`.
- Image: `review-backend:minikube-20260921163041-84f7f0afa3`.
- Runtime image ID: `sha256:eb2a34ee6db637603e7165aef86086961090aef23e45b538b9ad8461541bb75f`,
  matching `deployment.json.runtime_image_ids.review-backend`.
- Deployment build image ID: `sha256:938594b04bfcab3a0ad9cc08560dafbb3633b99d2b759b6c12ee852062dc645a`.
  Build and CRI runtime IDs are separate records, not interchangeable digest formats.
- Installed `model.py` and `coordinator.py` SHA-256 values matched the local files
  **before** the diagnostic edits. Deployment state files were not rewritten.

The Pod/container identity was unchanged before and after sampling. Current-container
logs contain the following events; all occur after its start time:

| Event | UTC timestamp | Evidence |
| --- | --- | --- |
| `model_ready` | 07:31:26.196 | Startup completed |
| `review_started` | 07:43:26.815 | Queue wait 29 ms |
| `review_finished` | 07:48:26.858 | `inference_timeout`, duration 300072 ms |
| `model_generation_finished` | 07:48:27.147 | Generation duration 300224 ms, 139 tokens |

The review ID is `4e994476-b6c7-4629-906f-f8897fbe6268`. A read-only SQLite
metadata query confirms `failed` / `inference_timeout`; no queued/running reviews
were present. The generation event has no review ID. Association is supported by
the single inference worker, the only review start/finish in these logs, and the
adjacent timestamps, rather than by an explicit ID in that event.

The newly measured cumulative `nr_throttled=2761` and `throttled_usec=127658733`
exactly match the supplied sample, while `nr_periods` has increased. Combined with
zero restarts and matching logs, this supports a common container instance. The
original sample has no supplied collection timestamp, so its exact interval cannot
be reconstructed.

## Confirmed execution behavior

The immediate failure is the shared 300-second generation deadline. It is not a
queue wait timeout, output token cap, model startup failure, or observed container
OOM. Summary/findings/suggestions produced 38/100/1 tokens against caps 72/176/136.
No cap was reached. This does not identify why generation was too slow.

Each section creates and tokenizes its own full prompt and invokes `generate`
independently. `use_cache=True` reuses attention state within a section; the code
passes no cache between sections. All three sections therefore perform their own
prefill. The installed Transformers 4.57.6 sampling loop runs a model forward,
converts final-position logits to float32, applies sampling, and only then invokes
stopping criteria. One token in the last section can include a full prefill and
sampling step before the deadline is observed.

`model_generation_finished.duration_ms` sums the generate-call wall times,
including prefill and decoding, excluding preceding prompt construction/tokenization
and subsequent text decoding/validation. `review_finished.duration_ms` starts at
job creation. The two durations must not be subtracted as if their origins match.
The log timestamps show the generation event about 289 ms after the failure event.
The coordinator sets cancellation, drains the one worker, and recovers readiness;
it does not forcibly interrupt an executing CPU operator.

`torch.set_num_threads(2)` is called during model load on the same single-worker
executor used for reviews. This configures intra-op parallelism, not total process
threads or exclusive CPUs. The live process had 18 OS threads and affinity `0-9`;
the container quota remained `200000 100000` (two CPU-seconds per second), with
no exclusive two-core binding. The serving worker's effective PyTorch intra/inter-op
getters were **not measured** in the existing process. A separate diagnostic Python
process would not establish the serving worker's settings.

The actual environment is Apple M2 Pro, Linux aarch64, torch `2.8.0+cpu`,
Transformers `4.57.6`, huggingface-hub `0.36.2`, safetensors `0.8.0`.
The node exposes `bf16` and `i8mm` CPU flags. The serving process maps Arm Compute
Library and OpenMP libraries. A separate metadata-only import of the installed
PyTorch binary reports oneDNN 3.7.1, OpenMP, OpenBLAS, `USE_MKLDNN=ON`,
`USE_CUDA=OFF`, and `CPU capability usage: DEFAULT`.
These show available capabilities and build support, **not** which BF16 kernels
executed this review. Neither `DEFAULT` nor aarch64 alone proves a fallback or an
accelerated path. No operator/kernel trace was available. Model configuration and
code retain BF16 CPU placement; no dtype or inference setting was changed.

## Resource evidence and limits

These are post-failure observations, not samples taken during the failed review.

| Scope | Measured evidence | Interpretation / limitation |
| --- | --- | --- |
| Backend, 08:13:40.519–08:14:02.863 UTC | 22.344 s interval; CPU usage delta 0.899 CPU-s; throttled-period/time deltas both zero; major faults unchanged at 58; memory PSI averages zero | Mostly idle, including diagnostic exec overhead. Does not establish historical inference contention. |
| Backend memory | `memory.current` ~608 MiB; `memory.peak` ~1.16 GiB; limit 6 GiB; max/oom/oom_kill all zero | No observed container-limit OOM. Does not exclude ancestor/host memory pressure. |
| Serving process memory | RSS ~3.87 GiB; file PSS ~3.28 GiB; anonymous PSS ~569 MiB; swap 0 | Cgroup charge and process RSS are different accounting views; ~608 MiB is not total model residency. Exact ownership of all mapped page charges was not traced. |
| Node parent cgroup | CPU quota `600000 100000`; memory limit 10 GiB; later memory.current ~8.13 GiB; memory.events max=2057, oom/oom_kill=0 | Historical limit encounters are real, but their timing relative to this review is unknown. |
| Docker VM | 10 visible CPUs, ~15.60 GiB reported memory; Linux 7.0.12-linuxkit | Visible CPUs/memory are not all reserved for this application. |
| Node / VM current activity | Load ~0.66; CPU PSI avg10 ~0.16%; memory/IO PSI avg10 0%; Docker stats ~25.3% CPU for its one running container | Low sampled CPU competition; not proof of low competition during inference. |
| Kubernetes | Allocatable advertises 10 CPUs / ~15.60 GiB, despite outer minikube limits of 6 CPUs / 10 GiB; running pod CPU requests total 2.975 cores | Requests and allocatable are scheduling data, not actual usage; outer cgroup limits still constrain execution. |
| Host around 08:12 UTC | M2 Pro, 10 cores, 16 GiB RAM; ~74.86% sampled CPU idle; ~7.25 GiB compressor; ~11.65 GiB swap used; memory-pressure free percentage 35% | Substantial compressed/swapped state; current swap use is not evidence of swap I/O during this review. |
| Metrics API | Kubernetes metrics endpoint unavailable; `top pods` unsuccessful | Per-pod current CPU/memory usage via metrics-server: `not_measured`. No components were installed. |

Current other node workloads comprise control-plane/network/storage components and
this project's DynamoDB/frontend. Host activity includes the virtualization process,
IDE, window server, browser, and Codex. Their activity during the review was not
recorded. A Docker stats working-set value also excludes cache, unlike raw
`memory.current`; these must not be compared as identical measures.

Cumulative `2761 / 15502` is a ratio of throttled enforcement periods, **not** the
fraction of this review blocked. Likewise 127.66 seconds of cumulative throttling
cannot be assigned to this 300-second review without a before/after baseline.
Hierarchical limits and per-CPU accounting further prevent equating these counters
with review wall-clock delay.

## Remaining hypotheses

- CPU quota/scheduling contention may have reduced throughput. Historical throttling
  exists, but inference-window CPU usage, throttle deltas, and run-queue evidence
  are missing.
- Repeated prefill may dominate one or more sections. The implementation establishes
  that it occurs; existing logs cannot quantify its share.
- BF16 operator dispatch, conversions, memory bandwidth, or sampling work may be
  expensive. Hardware flags and linked libraries do not settle this.
- Host compression/swapping or node reclaim may have contributed. Post-failure
  host state and cumulative ancestor memory-limit events are insufficient to time
  that contribution.

139 tokens / 300.224 seconds is about 0.463 tokens/second **including repeated
prefill**. It is not a measured steady-state decode rate. No parameter adjustment
is justified as a confirmed fix by the current evidence.

## Minimal local diagnostics and offline validation

The existing aggregate event now also records fixed numeric fields:

- `section_prepare_ms`: prompt construction and tokenization time.
- `section_input_tokens`: token count only; no prompt or token IDs.
- `section_generation_ms`: each generate call, also measured if it raises.
- `section_first_token_ms`: time to the first stopping-criteria callback, including
  prefill, first-token sampling, and callback overhead; not pure prefill timing.
- `worker_intraop_threads` / `worker_interop_threads`: effective getters called in
  the serving worker; no thread settings are modified.

Unobserved values are JSON `null`, never fabricated zero-time success. Existing
per-section output counts remain available; a raised generate call has no returned
token count, so its existing zero count must not be interpreted as proof that no
internal work happened. Generation exceptions and deadlines still fail normally.
The formatter exports only these fixed fields, not tensors, source, prompts,
model output, seeds, credentials, or exception strings. API responses are unchanged.

Offline tests exercise separate preparation/first-token/total timings, unchanged
sampling and budgets, deadline failure in the third section with partial counts,
exceptions with elapsed time but no fabricated completion, unobserved sections,
and formatter redaction. No real model was loaded by these tests.

Executed validation: 85 tests passed across `backend/tests/test_model.py` and
`backend/tests/test_api.py`; Ruff lint and format checks passed for the four changed
Python files; Python compilation, shell syntax checks for the two minikube wrappers,
and `git diff --check` passed. Two existing Starlette/httpx/AnyIO deprecation warnings
were emitted. Runtime performance validation of the new diagnostics: `not_executed`.

## Next single controlled measurement (not executed)

1. Obtain separate authorization to deploy only the diagnostic backend change,
   after checking ownership and an idle queue. Preserve all current settings and
   record the new image/Pod/container identity and model-ready time. Source edits
   deliberately change the backend build fingerprint; do not rewrite saved build
   evidence to pretend the old image contains this instrumentation.
2. Collect a short idle baseline, then one authorized review with unchanged input
   and settings. Record its ID and timestamps without capturing source or output
   bodies. Do not automatically retry or run the full verify workflow.
3. Sample timestamped container **and ancestor** CPU usage/throttling/PSI, memory
   events/PSI and faults, node load/CPU/I/O, and host compression/swap I/O deltas
   throughout that same interval. Keep units and cgroup paths/identity explicit.
   Record thread CPU times/affinity and effective worker getters. Sampled monitoring
   overhead must be noted.
4. Compare per-section preparation, first-token, remaining-generation time and token
   counts with resource deltas. A first-token callback is only an approximation to
   prefill plus sampling; exact operator timing needs a separately authorized,
   bounded profiler capture with no input/tensor/body recording. Verify installed
   BF16 operator/backend dispatch before claiming hardware acceleration.
5. Record success only if the unchanged quality checks produce a real completed
   review. Otherwise retain the actual error and all incomplete/unmeasured states.
   Stop after that single attempt; evaluate any configuration experiment separately.

References: [PyTorch intra-op threads](https://docs.pytorch.org/docs/2.8/generated/torch.set_num_threads.html),
[effective intra-op getter](https://docs.pytorch.org/docs/2.8/generated/torch.get_num_threads.html),
[Linux bandwidth control and hierarchy](https://docs.kernel.org/scheduler/sched-bwc.html),
[cgroup v2 accounting](https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/cgroup-v2.rst).
