# Single CPU inference measurement — 2026-09-21

Historical report: results apply only to the dates, models and environments stated below. This document is not evidence that the current checkout was revalidated. Audience: maintainers investigating prior work; prerequisite: [current documentation index](../README.md).

Status: `measured_with_observation_gaps`. Review outcome: `failed` /
`inference_timeout`. Optimization status: `not_validated`.

This is a new, authorized measurement following the earlier
[investigation](minikube-inference-investigation-2026-09-21.md). The earlier report describes
its own historical container and must not be read as the current deployment state.
[Sanitized evidence and counter samples](../evidence/minikube-cpu-measurement-2026-09-21.json)
are retained separately from deployment and acceptance state.

## Scope and identity

No applicable `AGENTS.md` was found in the repository or its ancestor directories.
Git status was checked first; all pre-existing changes were preserved. This task
adds this report and its evidence JSON only. Existing timing instrumentation was
sufficient; application code, manifests, prompts, settings and saved build records
were not changed. No build, deployment, restart, full verify, cluster lifecycle
operation, Docker configuration change, AWS access, or remote image push occurred.

The explicitly selected profile/context was `minikube`, namespace
`local-review-demo`, using a temporary private kubeconfig without changing the
user's current context. Ownership of the namespace, backend Deployment/ReplicaSet,
frontend Deployment/Service and all three PVCs was checked. Completed deployment
state, source fingerprints, local image identity and loaded CRI image proof were
checked read-only before authenticating or submitting a review.

| Identity | Value |
| --- | --- |
| Cluster UID | `87a4de40-9556-4415-bd6c-551ce09b2f92` |
| Namespace UID | `9f452678-fb0f-4088-be6d-6bdb201bcf67` |
| Backend Pod | `review-backend-844686f484-vf56m` |
| Pod UID | `cb0c09c9-6884-49da-9754-741b7062fa6a` |
| Container ID | `86ed001333c4fe938e7d56cdaa08b03fa57ef8555e6d76fbfbbfe98723d11451` |
| Started / restarts | `2026-09-21T08:23:11Z` / `0` |
| Image tag | `review-backend:minikube-20260921172120-2a4c027df7` |
| Runtime image ID | `sha256:6b1fd1e105e8194dea74b3c82d02c0c1dee8d2d7940e55634b4027542bdd7260` |
| Local build image ID | `sha256:01294f515174a90ce5101abb18e6acadf7bf2eba3be14a372d4dfd7657bb8c0f` |
| Deployment attempt | `16fcff03766dea053c47d384` |

Identity and cgroup inode matched before and after the experiment. Current-container
logs include the user's preceding review `e07aa6cc-d4d6-4973-9af3-65497c6c9b36`
and the new measurement, both after container start. Installed model/coordinator
source hashes matched local sources. Log association for generation events is
based on the single worker and adjacent review events; those aggregate events do
not themselves contain a review ID.

## Actual configuration and execution boundaries

Actual environment and container resources, not just repository defaults:

- `Qwen/Qwen3-1.7B`, revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`.
- CPU/BF16, `MODEL_CPU_THREADS=2`, timeout 300 s, output budget 384.
- Backend CPU request/limit 2; memory request 4 GiB / limit 6 GiB.
- Worker logs: intra-op 2, inter-op 10. These are effective getters in the serving
  inference worker. Inter-op 10 is a pool setting, not measured ten-core execution.
- `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS` were unset.
- Host: Apple M2 Pro, 10 physical cores, 16 GiB RAM. Docker VM/node/container are
  native aarch64; guest CPU flags include `bf16` and `i8mm`; affinity spans `0-9`.
- Docker reports 10 CPUs and 16,745,824,256 bytes. Outer minikube cgroup quota is
  6 CPUs and memory limit 10 GiB. Kubernetes allocatable reports the larger VM
  values, not those outer cgroup limits. Container and Pod each have a 2-CPU quota.
- PyTorch `2.8.0+cpu`, Transformers `4.57.6`, huggingface-hub `0.36.2`,
  safetensors `0.8.0`. Installed build metadata confirms oneDNN 3.7.1, OpenMP,
  OpenBLAS, MKLDNN enabled, CUDA disabled. Serving mappings include ACL/libgomp.

`torch.set_num_threads(2)` runs during model load on the same single-worker
executor used for reviews. It is not a binding to two exclusive physical cores.
PyTorch's OpenMP setter affects its thread context, and separate native contexts
can have their own pools. This is a reason to investigate pool initialization,
not proof that a particular library used ten threads.
[PyTorch 2.8 implementation](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/aten/src/ATen/ParallelOpenMP.cpp)
and [threading documentation](https://docs.pytorch.org/docs/2.8/notes/cpu_threading_torchscript_inference.html).

The first-token clock starts at the generate-call boundary. The first stopping
callback occurs after model forward and token sampling. Its time is **included**
in `section_generation_ms`; adding it again would double-count. It is an
approximation to prefill plus first-token work, not an isolated prefill operator
measurement. Preparation covers prompt construction/tokenization. Each section
has its own full prompt and KV cache, with a shared deadline across sections.

Weights stay BF16 on CPU, but sampling logits are converted to float32 in the
installed Transformers loop. Aarch64 BF16 GEMM in PyTorch 2.8 has backend and
fallback branches; flags and successful BF16 operations do not prove which branch
ran. Actual operator/kernel dispatch, BF16 hardware instruction utilization, and
memory-bandwidth utilization remain `not_measured`.
[Versioned dispatch source](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/aten/src/ATen/native/CPUBlas.cpp).

## One submitted review

Before submission: Pod Ready, `/health/ready` returned 200/ready, and the global
queue contained no queued/running reviews. Readiness also excluded a draining
worker. Existing acceptance credentials were consumed privately for normal login,
Cookie and CSRF handling. A private frontend port-forward preserved the canonical
Host and Origin. The original `minikube_verify.SOURCE` sample was submitted once
with a new client request ID; no other review was created. A local submission guard
prevented retry after any uncertain observer failure.

Review ID: `e6d35cc3-eda1-4832-91a3-5ddeb78867b0`.

- Started: `08:40:06.187Z`; queue wait **25 ms**.
- Failed: `08:45:06.197Z`, `inference_timeout`, review duration **300035 ms**.
- Generation event: `08:45:06.684Z`, summed generate time **300465 ms**.
- Generated **137 tokens**; every section remained below its token cap.
- Cancellation/draining finished; readiness returned to 200/ready, liveness was
  alive, the queue was idle, and no `inference_stuck` event or restart occurred.
- A final normal authenticated GET confirmed the failed status; final logout
  returned 204. No successful completed review or browser acceptance is claimed.

| Section | Input tokens | Preparation ms | First token ms (included below) | Generation ms | Output tokens / cap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Summary | 208 | 20 | 12107 | 74757 | 38 / 72 |
| Findings | 217 | 5 | 12135 | 225708 | 99 / 176 |
| Suggestions | `not_measured` | `not_measured` | `not_measured` | `not_measured` | 0 / 136; not started |

After subtracting the first-token intervals, 276223 ms (91.9% of generation time)
remained in generation. Approximate subsequent-token wall times are 1.69 s/token
for summary (37 tokens) and 2.18 s/token for findings (98 tokens). These include
sampling and framework overhead; they are not isolated decoder-kernel times.
Tokenization and queueing are not the main observed cost. Prefill-only optimization
would leave most measured time untouched.

Compared with the preceding review, first-token times are similar (~12 seconds),
while summary/finding times vary materially. This variability and the measured
host activity make a single unpaired timing comparison insufficient to validate
an optimization.

## CPU, memory and contention

The counter window was `08:40:05.492Z`–`08:45:12.869Z` (307.375248 s), covering
submission, generation, cancellation and a few seconds of idle observation. It
includes lightweight diagnostic exec overhead and is not exactly the generation
interval. Seventeen snapshots include one idle baseline and sixteen measurement
snapshots. The container cgroup path and inode remained unchanged.

| Measurement | Delta / result |
| --- | ---: |
| Container `usage_usec` | **597589889 µs = 597.590 CPU-seconds** |
| Average CPU cores | **597.589889 / 307.375248 = 1.94417** |
| Active intermediate intervals | Approximately **1.97–2.00 cores** |
| Idle baseline | Approximately 0.040 cores |
| User / system CPU | 510.502 / 87.088 CPU-seconds; system ~14.6% |
| `nr_periods` / `nr_throttled` | +3074 / +2780 |
| Container `throttled_usec` | +260149785 µs |
| Pod `throttled_usec` | +253973462 µs |
| Outer node CPU | 660.081 CPU-seconds; ~2.147 cores under a 6-core cap |
| Outer node throttling | +2 events / +377412 µs |
| Other node CPU, approximate | ~0.203 cores after subtracting backend |
| Container/Pod/ancestor memory max/OOM events | **All zero deltas** |
| Container sampled `memory.current` | 510.9–1111.4 MiB |
| Container historical memory peak | 1080.2 → 1115.2 MiB, below 6 GiB limit |
| Process sampled peak RSS | ~4.36 GiB; cgroup charge and RSS differ |
| Container major faults / file refaults | +0 / +0 |
| Container total page faults | **+20536176**, predominantly minor faults |
| Node memory PSI / I/O PSI `some` total | +87.293 ms / +53.671 ms |
| Guest swap-in/out counters | +0 / +0 |

There is direct evidence that the backend uses essentially its whole 2-CPU budget
and is frequently throttled. This identifies an active constraint, but does **not**
prove that raising quota is the best fix or predict a speedup. The 260.15 seconds
of throttling is an aggregate scheduler counter, not 260.15 seconds of lost review
wall time. Pod and child throttling must not be added together. Node pressure
includes the backend itself and cannot all be attributed to other workloads.
[Linux bandwidth and hierarchy semantics](https://docs.kernel.org/scheduler/sched-bwc.html).

The outer node was far below its 6-core limit on average; substantial sustained
competition from other node workloads is not supported by this run. The old
ancestor memory-limit events did not increase. Kubernetes metrics-server remained
unavailable (`not_measured`); direct cgroup and proc measurements supplied these
results without installing anything.

Host VM statistics used 16 KiB pages and showed **activity during this interval**:
101092 swap-in pages (~1.54 GiB), 42236 swap-out pages (~0.64 GiB), ~184.02 GiB
of decompressed pages and ~185.24 GiB of compressed pages. The latter figures
count repeated page operations, not unique data or disk I/O. Swap occupancy fell
from ~11.61 to ~10.65 GiB. Host sampled CPU idle ranged 26.3–69.29%.
The host therefore has a real concurrent memory-pressure confound, despite the
container having no OOM/major-fault increase. Its contribution to this review
cannot be assigned precisely from system-wide counters.

## Thread churn: a stronger diagnostic lead

The node task-creation counter increased **279302** in the measurement window,
versus 87 over the 10.72-second idle baseline. That node-wide total cannot all be
assigned to the backend. However, direct `/proc/1/task` sampling establishes
backend-specific churn:

- `08:45:03.191Z`–`08:45:08.836Z` (inference tail plus drain/idle): 250 observations,
  18–26 simultaneous threads, **223 newly observed thread IDs**, 241 distinct IDs.
- Subsequent idle baseline over 5.63 s: always 18 threads and **zero new IDs**.

These are observed lower bounds, not an exact total number of created threads.
Short-lived threads can start and finish between samples. The first interval spans
both active inference and subsequent idle time; the database was already failed
when its final queue count was read.

Together with 20.5 million minor faults and 87 CPU-seconds of system time, this
supports investigating native thread-team creation/destruction and allocation or
weight-reordering overhead before simply allocating more CPU. It does not identify
the responsible operator/library. It also explains why a snapshot of 18 threads
or `worker_interop_threads=10` alone would miss important behavior.

## Observation limitations

The temporary observer twice failed in its container resource-reading command.
The saved samples have gaps of ~105.19 s and ~54.77 s. Thread enumeration can race
with short-lived threads; the final observer tolerated disappeared entries and
completed. The original command stderr was withheld, so the exact cause of each
failed read is not established. This was not evidence of an API authentication
failure or a model restart.

Only observation was resumed. No second inference was submitted. Cumulative
same-instance endpoints retain the total CPU/memory-event differences across the
gaps, but cannot locate transient peaks or scheduling behavior inside those gaps.
The CPU interval means spanning gaps are valid averages, not continuous samples.

A fresh metadata-only PyTorch import was performed after draining to inspect the
installed build. It did not load model weights or execute a tensor operation.
Its thread defaults were not substituted for the serving worker's measured getters.
No profiler, native call stack, allocator trace, or per-kernel timing was captured.

## Ranked assessment

1. **Confirmed:** CPU-budget saturation and low end-to-end generation throughput,
   primarily after the first token. **Leading mechanism hypothesis:** native
   threading/allocation work is wasting part of that budget. Backend thread churn,
   minor faults and system CPU support it; no call-site attribution yet proves it.
2. **Confirmed confound:** host compression and swap activity during the request.
   This can affect the VM even without a guest OOM. System-wide metrics do not
   establish its fraction of inference time.
3. **Not supported as the primary explanation:** queueing, Python tokenization,
   container OOM, output budget exhaustion, or sustained outer-node quota saturation.
4. **Unknown:** exact BF16 dispatch efficiency, accelerated instruction use,
   OpenMP/ACL/OpenBLAS pool ownership, temporary weight packing/reorder cost, and
   per-core host scheduling. Inter-op 10 alone establishes none of these.

## Smallest next experiment and conditional fix — not executed

**Preferred single-variable candidate:** explicitly set only `OMP_NUM_THREADS=2`
in the backend process environment **before importing PyTorch**, retaining the
existing `torch.set_num_threads(2)`, inter-op setting, two-CPU limit, BF16, timeout,
model, prompts, seed derivation, sampling, output budget and quality gates.
This is a hypothesis test, not a validated fix.

Evidence for this candidate: the global OpenMP environment is unset, ten CPUs are
visible, the measured worker setting is correctly two, and transient native threads
appear during generation. It tests whether other initialization/thread contexts
use an inconsistent default and repeatedly resize/create teams. GNU OpenMP uses
its environment/default for regions not overridden by the runtime; PyTorch's
current-worker getter does not enumerate every external native pool.
[GNU OpenMP defaults](https://gcc.gnu.org/onlinedocs/libgomp/OMP_005fNUM_005fTHREADS.html).
This does **not** assert that inter-op 10 created those threads or that ACL honors
this variable. If the responsible pool ignores OpenMP, the experiment may do nothing.

- **Expected mechanism, if confirmed:** fewer transient threads and less scheduling,
  allocation and system-CPU overhead; lower CPU-seconds/token and generation time
  without changing review quality. No numerical speedup is promised.
- **Risk:** another parallel region may slow down; reduction ordering can change
  generated text even with fixed seeds. The unchanged quality checks must still pass.
- **Preparation:** separately authorize the backend-only configuration update;
  verify ownership, empty queue and no draining worker; record the image/config
  identity and keep state truthful. No rebuild is needed solely for an environment
  experiment, but a controlled Pod replacement is required and is outside this task.
- **Comparison:** use one baseline A and one candidate B under separately authorized
  bounded measurement, each with the same sample and no automatic retry. This run
  is a useful baseline but its sampling gaps/host churn limit strict A/B attribution.
  Keep startup/cache state and host pressure comparable; collect CPU-seconds/token,
  first-token/subsequent-generation time, thread turnover, faults and host activity.
  If host activity changes materially, mark attribution `inconclusive` rather than
  claiming the environment setting fixed it. Do not add hidden warm-up reviews.
- **Decision:** retain the one-variable change only if thread churn and generation
  cost improve together with a valid completed review under the existing deadline.
  A partial generation, reduced throttling or readiness alone is not acceptance.
- **Rollback:** restore the prior backend environment/template through the owned
  deployment workflow after the queue is idle. Keep the existing image, account,
  signing Secret, history and PVCs; preserve both experiment records.

If that candidate does not reduce churn, stop configuration guessing. The next
necessary diagnostic is a separately authorized bounded native operator/thread
creation profile in the same serving process, recording only names/counts/times
and no tensors, input text or generated text. Inspect actual BF16 backend selection
and allocation/reorder paths. A targeted library/runtime integration fix should
follow that evidence, rather than an unverified dependency upgrade.

Increasing only the CPU limit (for example 2 → 3, with worker threads still two)
could later isolate quota effects because the current budget is saturated and the
outer node has sampled headroom. It can also just fund more wasted work and add
host contention; it is not the preferred first fix. Evaluate it only as a separate
single-variable experiment and restore limit 2 afterward if unhelpful. Do not
combine it with OpenMP changes. There is insufficient evidence to change dtype,
inter-op threads or the deadline; float32 would also increase model memory demand
under the existing 6 GiB cap. No such adjustment was made here.

## Validation and data protection

Only this English report and sanitized evidence JSON were added. Existing model
and API regression tests were run offline after the review drained, including the
metric-boundary/redaction/deadline tests: **85 passed**. Ruff lint and format checks,
Python compilation, shell syntax checks, and `git diff --check` passed. Two existing
Starlette/httpx/AnyIO deprecation warnings were emitted. No real-model test was run. Evidence
checks independently confirmed identity, counter arithmetic and the single review
record, and excluded credential/source/result fields.

Existing accounts, signing Secret, history and three PVCs were retained. The sole
new review remains an accurate failed record. Normal login/session operations were
used; no authentication or CSRF bypass, history replacement, cache deletion, or
state-file rewriting occurred. Application ready after draining is not inference
or browser acceptance.
