# B measurement: OMP_NUM_THREADS=2

Historical report: results apply only to the dates, models and environments stated below. This document is not evidence that the current checkout was revalidated. Audience: maintainers investigating prior work; prerequisite: [current documentation index](../README.md).

Result: **improved in this single comparison**. The B review completed in 191.941
seconds and passed the unchanged product and sample-specific acceptance checks.
Recommendation: **retain `OMP_NUM_THREADS=2` for now**. No rollback or configuration
change was performed by this task. One successful sample is not a repeatability,
load, persistence-restart, or browser acceptance result.

Baseline A: `e6d35cc3-eda1-4832-91a3-5ddeb78867b0`.
Candidate B: `acbba2e3-2c9c-40f6-8fe8-6af9e2ea8fc9`.

[Sanitized B evidence](../evidence/minikube-cpu-b-measurement-2026-09-21.json) and
[previous A report](minikube-cpu-measurement-2026-09-21.md) retain the measured
values independently of deployment/acceptance state files.

## Preconditions and identity

Applicable instructions and Git status were checked. No applicable `AGENTS.md`
was found; all existing changes were preserved. The existing `minikube`
profile/context and namespace `local-review-demo` were accessed through a private
temporary kubeconfig. Ownership and image/build evidence were checked read-only.

| B identity | Value |
| --- | --- |
| Cluster UID | `87a4de40-9556-4415-bd6c-551ce09b2f92` |
| Namespace UID | `9f452678-fb0f-4088-be6d-6bdb201bcf67` |
| Pod | `review-backend-68b84d4948-4fwgs` |
| Pod UID | `a0b35d3d-d63f-4fca-97d9-46f3cf7c22ee` |
| Container ID | `ce34b23e1df964313cca970f1312b37f3cdd529869fe9b221ac4b554a0421656` |
| Container started | `2026-09-21T12:55:32Z` |
| Restarts | 0 before and after |
| Image tag | `review-backend:minikube-20260921172120-2a4c027df7` |
| Runtime image ID | `sha256:6b1fd1e105e8194dea74b3c82d02c0c1dee8d2d7940e55634b4027542bdd7260` |

B uses the same image and installed inference/coordinator source hashes as A.
Actual environment values match A for the fixed Qwen3 model/revision, BF16,
`MODEL_CPU_THREADS=2`, timeout 300 seconds and output budget 384. CPU request/limit
remain 2 and memory request/limit remain 4/6 GiB. The resource, mount and security
configuration matches A's saved snapshot. Prompt, seed derivation and sampling
parameters are unchanged in the identical running code/image. MKL/OpenBLAS thread
overrides remain unset. The observed environment difference is the user-applied
`OMP_NUM_THREADS=2`.

The old A ReplicaSet was no longer present, so an exhaustive old/new Pod-template
comparison was unavailable. The required inference settings, image, code and
resources were instead checked against saved A runtime evidence. This does not
claim that every unrelated historical Pod-template field was compared.

Before submission, the new Pod was Ready, `/health/ready` returned 200/ready and
the global queue was empty. This also excluded a draining worker. Existing
acceptance-account credentials were consumed privately for normal login, Cookie,
Origin and CSRF handling. Exactly one original acceptance sample was posted with
a new client request ID; a submission guard prevented retries. No full verify,
application deployment/restart, or second inference was performed.

## Outcome and generation timings

B began at `13:05:20.030Z`, with queue wait 101 ms. Generation ended at
`13:08:31.863Z`; the completed review was logged at `13:08:31.870Z`.
The authenticated API returned `completed`, and the existing `completed_review`
validator checked the pinned model identity, persisted sample, unchanged product
quality rules and sample-specific relevance. No generated body was logged or
included in the evidence artifact.

| Metric | A | B |
| --- | ---: | ---: |
| Review outcome | failed / inference_timeout | **completed; quality checks passed** |
| Summed generation time | 300.465 s | **191.790 s** |
| Generated tokens | 137, incomplete | **203, all three sections** |
| Summary: generation / tokens | 74.757 s / 38 | **38.573 s / 38** |
| Findings: generation / tokens | 225.708 s / 99, interrupted | **90.501 s / 100** |
| Suggestions: generation / tokens | not started | **62.716 s / 65** |
| Summary first token | 12.107 s | **8.404 s** |
| Findings first token | 12.135 s | **7.362 s** |
| Suggestions first token | not measured | 7.848 s |
| Effective intra-op / inter-op | 2 / 10 | **2 / 10** |

B input tokens were 208/217/210 and preparation times were 13/8/12 ms.
No section reached its 72/176/136 token cap. First-token timings are included in
generation totals, not additional time. Summary latency fell about 48.4% for the
same token count; findings used about 59.9% less time while returning one more
token. A failed before completing the task, so the total-time comparison must not
be described as the speedup of two successful equal-output reviews.

B finished normally, so there was no timeout cancellation to drain. Final health
was 200/ready, the queue was idle, identity was unchanged, and logout returned
204. No `inference_stuck` occurred. The completed B record was retained alongside
all earlier history.

## Same-instance counter deltas

Each group's delta is computed solely from that group's own unchanged container
and cgroup instance. **No cumulative counter from A was subtracted from B.**
B had 20 snapshots, including the idle baseline and final sample, without A's
observer gaps. The B resource window includes a short period before submission,
status polling and the final ready check; it is slightly longer than generation.

| Resource metric | A | B |
| --- | ---: | ---: |
| Counter window | 307.375 s | 193.178 s |
| Container CPU usage delta | 597.590 CPU-s | **368.162 CPU-s** |
| Average CPU cores: usage / elapsed | 1.944 | 1.906 |
| Approximate CPU-s/generated token | 4.362 | **1.814 (58.4% lower)** |
| User / system CPU delta | 510.502 / 87.088 s | 331.141 / 37.021 s |
| Periods / throttled periods | +3074 / +2780 | +1932 / +323 |
| `throttled_usec` delta | +260149785 | **+2370809** |
| Outer node throttle events / time | +2 / +377412 µs | **0 / 0** |
| Container memory max/OOM/OOM-kill events | 0 / 0 / 0 | **0 / 0 / 0** |
| Sampled container memory.current | 510.9–1111.4 MiB | 550.9–1165.9 MiB |
| Historical memory peak at final sample | 1115.2 MiB | 1220.4 MiB |
| Sampled process RSS peak | ~4.36 GiB | ~4.42 GiB |
| Container major faults | 0 | 0 |
| Container total page faults | +20536176 | +27833889 |

CPU-s/token includes prefill, sampling, service work and measurement overhead; it
is an end-to-end efficiency indicator, not a pure decode-kernel benchmark. A and
B have different output lengths and completed section counts. Minor faults still
occur at substantial volume; the experiment does not demonstrate elimination of
allocation or memory-copy work. Cgroup charges and process RSS are different
memory-accounting views.

The decrease in throttling is a scheduler-counter comparison, not proof of a
257-second wall-time saving. Child and parent throttling must not be added.
The same two-CPU quota now supports a completed review; increasing CPU limits was
not necessary for this sample.

## Thread turnover

A's short inference-tail/drain window observed 223 new backend thread IDs in
5.64 seconds, with 18–26 simultaneous threads. B's 250-observation window at
approximately 15–20 seconds into generation saw **10 threads throughout and zero
new IDs**. Both B idle windows likewise saw 10 threads and zero new IDs.

The short windows cover different sections, so they are not an exact per-section
thread-creation comparison. The broader node-wide task-creation rate also fell
from about **908.7/s in A to 8.05/s in B**, near A's idle baseline of about 8.1/s.
Node totals are not exclusively attributable to the backend, and short-window
sampling can miss very short-lived threads. Together, these observations strongly
support reduced thread-team churn rather than a mere change in a logged setting.
Inter-op remained 10; setting it to 1 was neither needed nor tested.

## Host-pressure comparison and attribution limits

Host counters use 16 KiB pages. These are within-group deltas over the resource
windows, not differences between cumulative values across the two runs.

| Host activity | A | B |
| --- | ---: | ---: |
| Swap-in | 101092 pages / 1.543 GiB | **32587 pages / 0.497 GiB** |
| Swap-out | 42236 pages / 0.644 GiB | **0** |
| Compression page operations | 185.24 GiB | 32.91 GiB |
| Decompression page operations | 184.02 GiB | 35.60 GiB |
| Sampled CPU idle range | 26.3–69.29% | 36.49–69.45% |

Compression/decompression totals can count the same page repeatedly; they are
not unique data sizes or disk throughput. B also had a shorter window. Even
normalized by elapsed time, B had less host memory activity, so the runs were not
under identical host conditions. They occurred hours apart in different Pods.
A additionally had two sampling gaps; B did not.

The unchanged model/image/resource configuration, much lower CPU-s/token,
near-elimination of observed thread turnover, and successful unchanged quality
gate support retaining the OpenMP setting. They do **not** identify a particular
BF16 kernel, prove the exact library creating A's threads, or establish how much
of the latency reduction came from OpenMP versus host pressure. Actual accelerated
BF16 dispatch remains `not_measured`; no profiler or additional inference was run.

## Decision and validation

**Recommendation: keep the user-applied `OMP_NUM_THREADS=2`; do not roll it back
based on these results.** The setting improved this measured workload without
changing the worker's effective intra-op/inter-op settings, dtype, budget, deadline,
resource limits or quality rules. This is a single successful comparison, not a
claim of universal or statistically established performance. Further repeatability
or workload tests require separate authorization; none were automatically started.

This task added only this English report and the sanitized B evidence JSON.
Application code, configuration, deployment records, existing accounts, signing
Secret, prior reviews, model cache and PVCs were preserved. The only inference
mutation was the one new review through the normal authenticated API.

Offline evidence checks passed for: distinct A/B identities with stable identity
within each run, one submission/review start, unchanged source/image/resource
checks, cgroup identity, counter arithmetic, summed section timings/token counts,
and exclusion of credential/source/result fields. Temporary observer scripts
passed syntax checks. No application code changed, so application regression tests
were not rerun for this measurement-only task. No browser or persistence-restart
acceptance is claimed.
