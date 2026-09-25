# Minikube diagnostics and maintenance — 2026-09-20 to 2026-09-21

Audience: maintainers reviewing historical evidence. Purpose: preserve the original observations and limitations extracted from the minikube guide. Prerequisite: understand [current minikube procedures](../guides/minikube-demo.md). These results apply only to their recorded environment and time; no current validation is implied. The port-forward incident excerpt had no separate date in the source and is retained as an undated follow-up to that maintenance record.

## Historical environment and verification results (2026-09-20)

This record predates the advisory behavior described above. The resource findings and the fact that deployment was not performed remain historical evidence; they are not a live test of the current `up`. The advisory change was verified offline only on 2026-09-21, without deployment, real inference, or browser acceptance.

At the beginning of the deployment work, the user's home directory contained a `minikube` profile, but its container was temporarily absent. The user subsequently started the cluster. Actual runs of `doctor`, `up` with automatic target selection, and `up --profile minikube` all recognized **a Running minikube cluster with an available API**, then exited with code 1 because resource and version requirements were not met. Before/after comparisons confirmed that the existing minikube configuration, legacy deployment state, and global kubeconfig were unchanged. `stop` exited with code 0 and printed advice only.

| Measurement | Result |
| --- | --- |
| Host | Apple Silicon arm64, 10 CPU, 16 GiB; estimated available/reclaimable memory about 2.77–3.18 GiB |
| Docker VM | Native aarch64, 10 CPU / 15.6 GiB; observed usage about 0.66–0.67 GiB |
| Target node | Kubernetes reported 10 CPU / 15.6 GiB allocatable, but the Docker node's actual limits were only **2 CPU / 3.91 GiB** |
| Other workloads | About 0.85 CPU requested / 240 MiB memory requested; memory budget about 440 MiB; 6 containers had no memory limit |
| Additional application budget | About **2.145 CPU / 4.34 GiB requested, 6.84 GiB memory limits** (conservatively including init containers) |
| Disk | About 47.75 GiB free on the host and 351.22 GiB on the target node's backing filesystem; initial incremental budget 28 GiB, so disk space was not a blocker |
| Client/API versions | kubectl 1.36.4 / Kubernetes 1.34.0, two minor versions apart; the user must select a compatible client |
| Storage/CNI | Existing `standard` / minikube-hostpath; CNI configuration used bridge/firewall/loopback/portmap; NetworkPolicy enforcement was not confirmed |
| Pod metrics | Not measured (metrics API unavailable); actual Docker node usage was read, and missing metrics were not treated as zero |

**Remaining blockers were node CPU/memory quotas, host memory pressure, and client version skew.** The node's advertised allocatable resources are insufficient evidence: even if the scheduler accepts the Pods, the outer 3.91 GiB cgroup cannot accommodate the backend's 6 GiB limit. The script does not change these quotas or rebuild the cluster.

Validation recorded during that deployment work: 55 targeted minikube tests passed; script syntax, formatting, Kustomize rendering, and regression checks for 16 EKS resources passed; 96 backend tests passed and 1 real-model test was skipped (with 2 existing dependency deprecation warnings).

Application images were not built or loaded, application namespace/PVC/Secret resources were not created, model weights were not downloaded, and real reviews and browser acceptance were not run. Cold/warm startup times, peak model-container memory, and review duration were not measured. Static tests and historical records do not substitute for real acceptance. Real AWS resources were neither accessed nor modified.



## DynamoDB Local image identity and startup permissions

The pinned `amazon/dynamodb-local:3.1.0` arm64 image was inspected directly in the existing minikube node on 2026-09-21. Its recorded image contract is in `scripts/tests/fixtures/dynamodb-local-3.1.0-arm64.json`:

- Repo digest: `sha256:7ef4a2c45b58c2901e70a4f28e0953a422c2c631baaaf5e2c15e0805740c7752`; image config digest: `sha256:8d094d015d5c836b1839ed489567a5cad2292217d4621fb72e98abf359930cd6`.
- Default user: `dynamodblocal`, numeric UID/GID `1000:1000`; ENTRYPOINT: `java`; CMD: `-jar DynamoDBLocal.jar -inMemory`; WORKDIR: `/home/dynamodblocal`.
- `/` and `/home` are root-owned mode `0755`; `/home/dynamodblocal` is owned by `1000:1000`, mode `0700`; its `DynamoDBLocal.jar` is root-owned mode `0644`.

The failed Pod used UID/GID `10001:10001`, inherited the correct image working directory, and invoked the existing JAR by relative path. Neither `/data` nor `/tmp` covered the program directory. Its effective container runtime configuration confirmed the UID and working directory. UID 10001 could not traverse the `0700` home directory, so Java reported `Unable to access jarfile DynamoDBLocal.jar`. Specifying an absolute JAR path or repeating the working directory would not grant traversal permission.

The fix changes only DynamoDB's Pod `runAsUser` to the verified image UID 1000 and its permission init program's `/data` owner to 1000. GID/fsGroup stays 10001 to retain the existing volume group and avoid changing fsGroup. Only the dedicated volume root is chowned/chmodded, to `1000:10001` / `0770`; no existing database files are traversed or rewritten. The main container remains non-root with a read-only root filesystem, no privilege escalation, all capabilities dropped, and RuntimeDefault seccomp. The image version, relative startup path, local endpoint, invalid local credentials, telemetry setting, and table initializer are unchanged.

Existing files created under another UID with restrictive modes require an explicit access review; this fix does not recursively repair them, recreate PVCs, or empty a database. In the inspected failing environment, the dedicated DynamoDB data directory was empty before the update. Backend/model/history volume identities and permissions are outside this change. The recorded image evidence is arm64-specific; this repair does not claim an amd64 runtime test.

Offline regression tests resolve the effective container identity and startup path against the recorded image permissions, reproduce the old UID's traversal denial, and execute the rendered init program against an instrumented filesystem to verify root-only, idempotent permission changes. They also retain the existing hardening, local endpoint, ownership, diagnostic, and model-contract checks. Offline tests alone do not establish a running database.

The controlled runtime repair on 2026-09-21 used the already running `minikube` profile, cluster UID `87a4de40-9556-4415-bd6c-551ce09b2f92`, after verifying target, namespace, resource ownership, and the operation lock. An optimistic JSON patch changed only the owned `review-dynamodb` Deployment's UID and init argument. The existing Recreate strategy replaced its failing Pod; no PVC, Secret, image tag, or other workload was updated.

Runtime verification passed: Pod `review-dynamodb-649bf7ffbd-pw276` became ready; six observations over 75 seconds remained ready with restart count 0. Actual process UID/GID was `1000:10001`, effective capabilities were zero, `NoNewPrivs=1`, and the root filesystem stayed read-only. Host, Docker VM, Kubernetes node, and cached image all reported native arm64. Two consecutive initializer runs through an explicit loopback DynamoDB endpoint succeeded; `llm-review-users` was ACTIVE with `login_id` as its string HASH key. The table did not exist before initialization; the second run preserved its creation identity and schema, and the account count remained 0. No existing account or database file was removed.

All three PVC UIDs/specifications, signing Secret identity/value, other application Deployment specifications, target owner state, global kubeconfig, and minikube configuration were unchanged in before/after comparisons. The new dedicated repair report is `.local/dynamodb-startup-fix-62y_moa0/verification.json`; the earlier failed full-deployment report was not rewritten as successful. Script syntax, Ruff checks, Kustomize rendering, the 16-resource EKS rendering regression, and 125 offline minikube tests passed. Full application deployment, real-model review, and browser acceptance were not performed by this repair.



## OpenMP setting and measured evidence (2026-09-21)

The minikube overlay explicitly sets `OMP_NUM_THREADS="2"` on the `review-backend`
container. This retains the setting already applied by the operator for the B
experiment. `MODEL_CPU_THREADS=2`, BF16, the fixed model/revision, prompts,
generation parameters, output budget, 300-second inference timeout, CPU request/limit
of 2, memory request/limit of 4/6 GiB, and quality gates remain unchanged. The base
manifests and other containers do not receive this environment variable.

The saved [A report](../reports/minikube-cpu-measurement-2026-09-21.md) and
[B report and evidence](../reports/minikube-cpu-b-measurement-2026-09-21.md) support retaining
this setting for the measured workload:

| Measurement | A: OMP unset | B: OMP set to 2 |
| --- | --- | --- |
| Review | `e6d35cc3-eda1-4832-91a3-5ddeb78867b0` | `acbba2e3-2c9c-40f6-8fe8-6af9e2ea8fc9` |
| Result | `failed` / `inference_timeout` | `completed`; existing quality and sample-specific checks passed |
| Generation time / tokens | 300.465 s / 137, incomplete | 191.790 s / 203, all three sections |
| CPU seconds per generated token | 4.362 | 1.814 (about 58.4% lower) |
| Host swap-in / swap-out | 1.543 / 0.644 GiB | 0.497 / 0 GiB |

B's review duration was 191.941 seconds. Its short thread-observation window saw
zero new thread IDs, versus 223 in A's differently positioned window; effective
worker intra-op/inter-op counts remained 2/10. Each resource delta uses its own
unchanged container instance. CPU seconds/token includes prefill and service work,
and the different output lengths preclude an equal-output speedup claim.

B had less host swapping and compression activity, a shorter observation window,
and no sampling gaps; A had two gaps. This single comparison does not isolate the
OpenMP contribution from host pressure or establish repeatability. Actual accelerated
BF16 dispatch remains `not_measured`; architecture and successful BF16 operations
alone do not prove the kernel execution path.

Configuration retention is a repository-only change with offline rendering and
regression checks. It does not rebuild, deploy, restart, submit another review, or
rewrite saved deployment/acceptance records. B proves one real completed review
under the existing quality gates, **not a complete `verify` or browser acceptance**.
Full API/security/history-isolation acceptance, controlled restart/persistence
checks, network-policy enforcement checks, and browser acceptance still require
their own recorded results; this experiment does not establish them.



## Model-cache repair verification (2026-09-21)

The direct startup blocker was an incomplete pinned snapshot. The first shard was absent from
the snapshot and its blob existed only as a 2,443,182,080-byte `.incomplete` download. The official
revision requires 3,441,185,608 bytes for that shard. The second shard was present at 622,329,984
bytes. The cache filesystem had 365,525,118,976 free bytes; the observed backend UID/GID was
10001:10001 with readable cache files. Observed backend cgroup `max`, `oom`, and `oom_kill` counters
were zero. These observations do not establish the original download interruption's cause or
rule out past node-level pressure. The former unqualified `coordinator_failed` event did not
retain the underlying exception.

The official downloader resumed the first shard in the same PVC/cache without forcing a fresh
download. Both complete files matched the official pinned revision's sizes and SHA-256 values:

| Shard | Bytes | SHA-256 |
| --- | ---: | --- |
| `model-00001-of-00002.safetensors` | 3441185608 | `169ad53ec313c3a34b06c0809216e4fc072cce444a5d4ff2b59690d064130ed5` |
| `model-00002-of-00002.safetensors` | 622329984 | `912becff8d60672aa8628ef08c05898d9adf17c2ad4ae3caf99b065622fdeff9` |

The index matched the official Git blob `986d7db875b47d21f68530f6baac038f1b297b39`.
Every indexed tensor was verified using safetensors metadata. The second shard retained its inode
and modification time; the first retained the original partial file's inode after download
completion. No PVC was deleted or recreated. The temporary non-root repair Pod was removed and
the original backend replica count restored, with no second model loaded during repair.

Only the backend image was replaced, using the native arm64 local image
`review-backend:minikube-cache-fix-20260921-v2` in the existing `minikube` profile. Queue checks
returned zero before changes. The owned backend Service was briefly isolated during image
replacement to prevent new submissions, then its exact selector was restored. The first image
update passed startup but was rolled back when the pre-existing partial deployment state lacked
image maps. The final update preserved that target identity and merged only backend image records;
it did not rewrite historical full-deployment or acceptance results.

The final Pod `review-backend-546654c995-sfkqr` emitted successful tokenizer, weights, CPU placement,
and startup-generation stage events followed by `model_ready`. Its readiness endpoint returned
HTTP 200 with `{"status":"ready"}`, and Kubernetes reported Pod Ready with zero restarts.
These are actual backend startup results. A completed application review and browser acceptance
were **not run** during this repair.

Validation: the full backend suite passed 125 tests with one opt-in real-model test skipped and two
existing dependency deprecation warnings. A final invalid-index edge case was then added; all 30
cache/startup-focused tests passed. The minikube suite passed 126 tests. Ruff checks/formatting,
Python and shell syntax checks, minikube Kustomize rendering, and the existing 16-resource manifest
regression validator passed. Detailed local evidence, including both update attempts, is retained
under `.local/model-cache-repair-20260921/` (not committed).

Final stability observation lasted 60.8 seconds: all three samples reported Pod Ready, HTTP 200,
and restart count zero, with backend cgroup `max`, `oom`, and `oom_kill` counters still zero.
All three PVC UIDs and both DynamoDB/frontend Deployment resource versions were unchanged.
The temporary repair Pod was absent and the restored backend Service had a ready endpoint.
These memory observations are not evidence for reducing the unchanged 6 GiB backend limit.

Files changed for this repair: `backend/app/model.py`, `backend/app/model_cache.py`,
`backend/app/startup.py`, `backend/app/coordinator.py`, `backend/app/main.py`,
`backend/pyproject.toml`, `backend/requirements-dev.lock`, `backend/tests/test_model.py`,
`backend/tests/test_model_cache.py`, `backend/tests/test_api.py`, `scripts/minikube_demo.py`,
`scripts/tests/test_minikube_demo.py`, and this document. Existing README, frontend, manifest,
model-setting, prompt, and generation-contract changes were preserved.



## Port-forward incident and offline follow-up (date not separately recorded)

An offline macOS experiment with a real Python server subprocess reproduced
`EADDRINUSE` (errno 48) on a plain bind after server-initiated connection closure,
while reusable bind/listen succeeded. An active listener still prevented reusable
bind/listen. This supports reusable-listener probing for reconstruction; it does
**not** establish TIME_WAIT as the cause of the reported 8080 incident. Later empty
listener listings and a successful bind cannot reconstruct the earlier port state.


The supplied incident report records review
`2103c951-8f0d-4868-827e-cc258438d2e4` with `review_completed=true`, followed by
`status=failed` at `persistence_acceptance`, `persistence_verified=false` and
`api_acceptance_passed=false`. This repair does not rewrite that report or submit
another review. Offline regressions ensure reconstruction failure preserves the
completed-review fact without claiming persistence, full verification or UI success.
Actual same-cookie/history/cache persistence after restart, completion of the full
verification procedure, and browser acceptance still require separately authorized
runtime validation. Offline socket tests do not establish cluster forwarding success.

