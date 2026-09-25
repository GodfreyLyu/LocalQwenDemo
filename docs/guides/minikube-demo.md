# Deploy the real-model demo to a user-started minikube cluster

Audience: local Kubernetes operators. Purpose: deploy and accept the application on a user-managed cluster. Prerequisites: Python development environment, Docker, kubectl and a running native single-node minikube; explicit authorization for writes.

[Documentation index](../README.md) · Commands use the repository root unless a block explicitly changes directory. Review each section’s side effects before running it.

**You manage the cluster; the script deploys the application.** The script does not create, start, stop, delete, or rebuild minikube clusters, or change cluster CPU/memory quotas, Docker Desktop settings, CNI, or storage components. It preserves the pinned Qwen3 model, existing application constraints, and three separate persistent volumes.

A successful `up` establishes application readiness only. Run `verify` separately and obtain a real `completed` review to demonstrate successful inference. API acceptance does not establish browser acceptance.

## When to use this guide

`doctor` is a read-only diagnostic command. It retains the full resource calculations and returns nonzero for insufficient resources, missing measurements, scheduling risks, or excessive client/server version skew. It prints available figures and specific reasons; unavailable values remain `not_measured`.

`up` collects the same diagnostics but does not call `doctor` or use its exit status as a deployment gate. Running `up` means you want to attempt deployment. Host memory pressure, insufficient Docker/node CPU or memory, host/VM disk budget shortfalls, unavailable metrics, node readiness/taint risks, and client version skew produce English warnings and deployment continues. No confirmation, `--force`, or skip-check option is needed. Unknown inputs invalidate their dependent calculations; they are never treated as zero usage or sufficient capacity. Independent measurements continue even when one read fails.

Mandatory checks run separately and still stop deployment: a selected running local Docker-driver single-node minikube with a verified loopback API; consistent target/state/namespace/resource ownership; matching native host/Docker/node architecture; and an existing supported minikube-hostpath StorageClass with compatible existing PVCs. Existing local endpoint/credential restrictions, signing Secret validation, idle-queue protection, and resource ownership checks during apply remain in force.

Actual image build/load, local dependency initialization, Kubernetes apply, scheduling/readiness waits, or state-write failures still return nonzero. The error names the failed deployment stage. Failures are recorded and re-raised, never skipped, and no incomplete deployment is presented as ready. The script does not change quotas or application resources to make a diagnostic pass.

## Prerequisites and operating steps

First, manually start a local minikube cluster and confirm it is healthy. Prepare the Docker daemon, minikube, kubectl compatible with the target API server, and the Python 3.12 environment with `backend/requirements-dev.lock` described in [Local development](local-development.md). The host does not need PyTorch or Node; application images are built natively inside Docker.

```bash
# Replace this with the path to your checkout.
cd /path/to/LocalQwenDemo
scripts/check_minikube_demo.sh
scripts/minikube_demo.sh doctor --profile minikube
```

The following commands require authorization for this target. `up` builds/loads images, creates or updates application resources and may download weights; `verify` creates accounts/reviews and restarts owned workloads. They are not diagnostic commands.

```bash
# Repository root; only after target-specific authorization:
scripts/minikube_demo.sh up --profile minikube
scripts/minikube_demo.sh verify --profile minikube
scripts/minikube_demo.sh port-forward --profile minikube
```

Open `http://localhost:8080` in a browser. Register or log in, submit the sample, wait for a real result, and reopen it from history. Do not substitute `127.0.0.1`: Origin matching is exact.

- `doctor`, `up`, `verify`, `port-forward`, `status`, and `logs` use the same read-only profile discovery and Host/API/kubelet status checks. Omit `--profile` only when exactly one Host is Running; multiple running profiles require an explicit choice. The script never guesses from the global current-context.
- A missing or stopped profile, mismatched Docker node, or unavailable API/TLS/readyz check causes failure. The script does not fall back to another cluster.
- The supported target is an **existing native, single-node minikube cluster using the Docker driver**. Its API must have a loopback port verified against Docker metadata. Other drivers, multiple nodes, or unknown ports are rejected without changing configuration.
- `--minikube-home /path/to/.minikube` selects another existing minikube state directory. The default is the existing `MINIKUBE_HOME` or `~/.minikube`. The script does not initialize this directory.
- `up --port 8090` also updates ALLOWED_ORIGIN; subsequent commands read the port saved for that target. Port forwarding listens only on `127.0.0.1`, with no tunnel, LoadBalancer, NodePort, or LAN endpoint.
- `verify` uses the same localhost port and cannot run alongside foreground `port-forward`. Stop your own forwarding process with Ctrl-C first, then restart it after acceptance. The script does not terminate unknown processes holding the port.
- Repeated `up` runs reuse accounts, the signing Secret, history, and the model cache. If source/build inputs match the recorded fingerprint and the host image's native architecture and image ID also match, image layers are reused and reloaded under a new unique tag. Otherwise, images are built normally. Images are not pushed to a remote registry.
- `--cold-timeout` defaults to 3600 seconds and adjusts the backend startup probe budget as well; `--warm-timeout` defaults to 600 seconds. The first startup includes downloading weights; warm startup reads the complete pinned revision snapshot. Application inference always has a 300-second timeout.
- `verify --skip-restart` is diagnostic only. It exits nonzero because persistence acceptance is incomplete and must not be presented as a full pass.

```bash
scripts/minikube_demo.sh status --profile minikube
scripts/minikube_demo.sh logs --profile minikube
scripts/minikube_demo.sh stop
```

`stop` is retained for compatibility and advice only: **it executes no commands and writes no state**. You manage cluster startup and shutdown; consider other projects sharing the cluster before acting. `up` cannot start a stopped cluster. The former `--cpus` / `--memory` options have been removed.

[Historical diagnostics, repairs, and their limitations](../reports/minikube-maintenance-2026-09-20-to-21.md) are separate from this procedure.

## State and ownership protection

The entire cluster need not belong to this project. Application resources are created only in the dedicated `local-review-demo` namespace. Existing namespaces or resources with conflicting ownership are not adopted.

State is private and shared across checkouts. The default root is
`${XDG_STATE_HOME:-$HOME/.local/state}/local-qwen-demo/`; it follows the user-level
state convention and survives moving or downloading a checkout. Override it with
`LOCAL_QWEN_STATE_HOME=/absolute/path` or `--state-root /absolute/path` (the flag wins).
Use the same root for all checkouts managing a target; separate roots do not share locks.

```text
<state-root>/locks/<target-hash>.lock
<state-root>/targets/<profile>-<minikube-home-hash>/<cluster-UID>/
  owner.json
  kubeconfig
  plan.json
  startup.json
  deployment.json
  verification.json
  undeployment.json
  acceptance-account.json
  attempts/
```

Home/profile/cluster UID select a target; namespace UID and the random ownership
marker authorize resource operations. The `root` field is historical source information,
not a checkout ownership requirement. A new build also records `source_root`. Identical
names or labels never authorize adoption. A recreated cluster gets a separate target
and leaves the old history intact. A missing namespace is not a successful deployment:
`up` archives old evidence before creating a new namespace incarnation, and does not
claim that missing data survived.

The root, target directories and target lock are private; state files use mode 0600.
Kubeconfig and acceptance-account files contain credentials. Do not publish the shared
state, print those files, or put it in Git. Different checkouts use the same target lock,
including migration and cleanup. Diagnostics also take this local lock but do not mutate
cluster resources. Interactive forwarding releases the operation lock after validation;
cleanup never discovers or kills an unknown process by port number.

### Import an existing checkout's deployment

Stop using the old deployment scripts after import; they do not know the shared lock.
Import validates home/profile/cluster UID, namespace UID, the random marker, expected
resource ownership and identity consistency in the source records. The old operation
lock is held during copying. The original files remain intact; a private staging copy
is published atomically. An interrupted copy can be retried; incomplete staging folders
are never used as target state. An existing different target is never overwritten or
merged. Repeating a completed import leaves newer shared records intact.

A matching legacy target inside the **current checkout** can be imported automatically
when shared state is absent. No other personal directory is searched. If its namespace
is absent, legacy files are preserved as historical evidence without adoption. Explicit
imports require a live matching namespace; incomplete or conflicting identity evidence
must be investigated, not repaired by editing JSON or deleting owner.json.

For the existing deployment originally created from `local-llm-code-review`:

```bash
cd /Users/godfreylyu/DemoLLMProject/LocalQwenDemo
scripts/minikube_demo.sh import-state --profile minikube \
  --from-state /Users/godfreylyu/DemoLLMProject/local-llm-code-review
scripts/minikube_demo.sh up --profile minikube
```

`--from-state` accepts an old checkout, its `.local/minikube-demo` root, or an exact
target directory. The selected cluster must already be running. An import is a state
copy, **not** a build, deployment or acceptance run. It never rewrites fingerprints,
image IDs, review results or failure statuses. Since these two checkouts have different
build inputs, run `up`: it computes current fingerprints and uses the normal unique-tag,
local build/load and Docker/CRI image verification path. `verify` still rejects changed
source inputs until that deployment finishes. Unchanged source in a relocated checkout
can use the trusted existing deployment records without inventing new proof.

Once imported, subsequent checkouts need only the same state root and target:

```bash
cd /path/to/another/LocalQwenDemo
scripts/minikube_demo.sh status --profile minikube
scripts/minikube_demo.sh up --profile minikube
```

The script generates a temporary private kubeconfig with one context from the selected minikube profile's local client certificate/key, CA, and Docker loopback API port. It does not read AWS exec credentials from the global kubeconfig, invoke update-context, or change the global current-context. After mandatory checks pass, `up` rechecks ownership under the target operation lock and saves a private kubeconfig with mode 0600 in the target directory, even if resource diagnostics failed. All Kubernetes operations explicitly specify context and namespace. Read-only cross-namespace Pod resource checks project only resource fields such as requests/limits; they do not read other projects' environment variables, commands, or application content.

Commands such as `up` and `verify` use a per-target operation lock. Before recreating application Pods, the script checks that all users' queues are empty, pauses this application's frontend, and checks again to prevent new-submission races. It does not modify other projects' Deployments, PVCs, Secrets, namespaces, nodes, or components.

## Resource budget for an existing cluster

The host no longer needs an additional 9 GiB + 2 GiB to create a new cluster. Preflight reports host, Docker, existing-node, and incremental application resources separately. Unavailable measurements use the stable report value `not_measured`.

### Unchanged application requirements

The backend requests **2 CPU / 4 GiB** and has limits of **2 CPU / 6 GiB**. DynamoDB Local requests 100m / 256 MiB and has limits of 500m / 512 MiB (JVM heap 256 MiB). The frontend and permissions init containers are counted separately. The model still uses BF16; older macOS measurements have not been used to reduce the Linux container limit.

Scheduling and memory safety are checked separately. These thresholds determine the diagnostic verdict; they are warnings for `up`, not permission to resize the cluster or change the application:

1. Diagnostics check that the node is Ready, schedulable, and free of taints the application does not tolerate, and that kubectl and the target server differ by no more than one minor version. These findings are advisory for `up`; actual rollout waits still fail on unsuccessful scheduling/readiness. Matching native host, Docker VM, and node architectures remains mandatory.
2. Use the smaller of node allocatable resources and actual Docker node CPU/memory quotas (including NanoCpus, Quota/Period, and CPUset for CPU), then subtract requests from other nonterminal Pods to check whether the application fits. Pending Pods, Pod overhead, and init containers are included. Init and main containers are conservatively counted as concurrent to cover native sidecars, so this exceeds the normal scheduling requirement for sequential init containers.
3. Check capacity against other Pods' memory requests/limits, this application's memory limits, and a **512 MiB node margin**. Containers without memory limits are counted separately; their requests are only a lower bound, not a measured maximum.
4. Also read actual memory usage for the Docker node and all running containers, Docker quotas, and Pod metrics when available. Actual node usage plus incremental application memory must fit within the smaller of node capacity and allocatable memory, minus 512 MiB.
5. Existing application Pods are excluded from other workloads only after the Deployment→ReplicaSet→Pod ownership chain is verified. Labels alone do not qualify for a capacity deduction. Incremental memory is `max(target application memory limits - measured existing application usage, 0)`, avoiding counting the entire existing model process twice.
6. If metrics-server is unavailable, the report says so. Verified application Pods' cgroup usage is used to calculate the redeployment deduction. If that usage is also unavailable, the script refuses to infer the incremental requirement. On an initial deployment with no application Pods, the increment is the full application budget. Missing metrics still produce a nonzero `doctor` result; `up` retains the missing-data warning and attempts deployment, including when no redeployment deduction can be calculated.
7. Estimated host free/inactive/speculative memory (macOS) or MemAvailable (Linux), and total Docker quota minus current usage, must each cover incremental application memory plus a **1 GiB margin**. Errors distinguish host memory pressure, insufficient Docker quota, and insufficient target cluster/node capacity, with the underlying resource figures. These estimates do not guarantee performance; other workloads without memory limits may consume more resources later.

An existing 2 CPU / 4000 MiB minikube cluster therefore remains insufficient for the backend and its dependencies even when running. You decide how to provide the required resources. The script neither resizes nor rebuilds the cluster. It still reports the insufficient resources; `doctor` fails and `up` attempts deployment with warnings. Scheduling, OOM, disk exhaustion, and rollout timeout can still cause an actual deployment failure.

### Incremental disk budget

There is no new-cluster image or control-plane overhead. The initial uncached budget is **28 GiB**: 4 GiB for layers/copies and 3 GiB for build scratch space per application image requiring a build (14 GiB for two images), 4 GiB for missing pinned weights, 4 GiB for transfer scratch space, and 6 GiB for data growth and safety.

Each confirmed reusable application image with matching source fingerprint, architecture, and image ID reduces the build budget by 7 GiB. A complete pinned snapshot verified through the owned backend reduces it by another 4 GiB. Even when both images and the model are reused, **10 GiB** remains reserved in the budget for transfers and safety. These are conservative estimates, not measured disk usage. Similar images belonging to other projects do not establish reusability.

The host filesystem and the target Docker node's backing filesystem are checked **separately**. Free or maximum capacity in Docker's sparse virtual disk cannot substitute for host free space. PVCs request 10 GiB for history, 12 GiB for the model cache, and 1 GiB for DynamoDB; hostPath does not guarantee reservation or enforcement of those sizes. Old images, model files, and build caches are not automatically cleaned up.

## Storage, network, and security differences

- The default is the existing `standard` StorageClass. Use `--storage-class NAME` to select another existing `k8s.io/minikube-hostpath` class. Unknown provisioners are rejected. The script does not install provisioners, create or modify StorageClasses, change existing PVC classes, or use gp3/EBS.
- Application containers remain non-root with read-only root filesystems and no privilege escalation; backend UID/GID/fsGroup=10001, frontend UID=101, and DynamoDB UID=1000 with the existing data GID/fsGroup=10001. Because hostPath does not guarantee fsGroup directory initialization, local init containers use only CHOWN/FOWNER to prepare dedicated PVC root directories, without recursively rewriting existing files.
- Pod recreation and manually stopping/starting the same cluster normally preserve volumes. Deleting a profile/PVC or Docker data, or resetting Docker Desktop, may lose data. There are no EBS encryption, backup, or disaster-recovery guarantees.
- Persistent `amazon/dynamodb-local:3.1.0` is deployed first. A temporary loopback forward reuses `init_local_users.py` to create the table idempotently and check ACTIVE status and the `login_id` HASH key before starting the backend. The initializer's loopback-only protection remains intact.
- The backend uses the explicit endpoint `http://review-dynamodb:8000` and invalid `local` credentials. AWS metadata/shared configuration is disabled; host AWS profiles and HF tokens are not inherited. DynamoDB Local telemetry is disabled.
- SIGNING_SECRET is randomly generated and passed through stdin to create the Kubernetes Secret. It is not printed, placed on the command line, or saved as a local plaintext file. Repeated deployments reuse the existing value. A missing key or ownership mismatch fails explicitly without automatic rotation.
- The local Nginx overlay proxies only `/api/` and `/health/` to the backend, preserving paths, Origin, Cookie, CSRF, and security headers. API routes never use the SPA fallback. Only local `ENVIRONMENT=local` / `COOKIE_SECURE=false` allows HTTP; production HTTPS/Secure Cookie validation remains unchanged.
- The node's actual `/etc/cni/net.d` configuration is inspected. Calico/Cilium are recorded as `capable_not_verified`; bridge/Kindnet/Flannel as `not_enforced`; unknown plugins as `unknown`. **CNI components are never installed, replaced, or reconfigured.**
- NetworkPolicy YAML still describes frontend→backend, backend→DynamoDB, necessary DNS, and backend→public HTTPS download access. Standard NetworkPolicy cannot precisely restrict Hugging Face/CDN domain names. Without policy enforcement, these rules do not provide isolation, and the report says so. Such an environment must not be presented as safe for untrusted multitenancy.

## Success criteria and real inference acceptance

The minikube overlay retains `OMP_NUM_THREADS=2`; see the [model reference](../reference/model.md) for the model contract and [dated measurements](../reports/minikube-cpu-b-measurement-2026-09-21.md) for its limited evidence.

### Acceptance procedure

The model remains pinned to `Qwen/Qwen3-1.7B` / `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, using TransformersModel on CPU, Torch 2.8.0, BF16, 2 threads, `trust_remote_code=False`, safetensors, `enable_thinking=False`, 2048 input / 384 total output tokens, a 300-second timeout, one worker, and concurrency 1. Three-section generation, prompts, quality gates, authentication/CSRF, user history isolation, rate limits, and the durable queue remain unchanged. There is no fake model, external inference, or automatic float32 fallback. Weights go only into the PVC, not images or Git; the host cache is not moved.

`verify` checks the homepage and security headers through the same localhost entry point, live/ready JSON, and 401 responses for unauthenticated API requests. It registers/logs in a dedicated account, checks Cookie/CSRF behavior and rejection of an incorrect Origin, and submits the fixed `average(values)` sample. Acceptance requires a real `completed` result, the correct model/revision, valid Summary/Findings/Suggestions, the existing quality checks, and mention of the sample's empty-input/division-by-zero issue. A failed, timed-out, or invalid_model_response result, HTTP 200 alone, or a Running Pod is not success. The script does not retry blindly.

It then checks history, repeat login, and isolation from a second user. With an idle queue, it performs controlled recreation of only this project's backend/DynamoDB Pods and verifies that the old Cookie, account, history body, and pinned weight file size/mtime/inode survive. It records review duration, cgroup current/peak memory, and warm recreation time. Unavailable measurements are recorded as `not_measured`.

Network acceptance is recorded separately: allowed paths must succeed; the prohibited backend→frontend path is probed with a Python socket, and only an actual timeout counts as observed blocking. A CNI reported as policy-capable fails acceptance if it allows that path. A CNI without policy support may still allow product inference acceptance to proceed, but `network_policy.enforcement_verified=false` and network isolation must not be reported as passed.

`startup.json` / `verification.json` contain only status, target identity, durations, resource figures, and opaque review IDs, with no source, model response body, Cookie, or CSRF value. The dedicated account is saved only in `acceptance-account.json` with mode 0600; open it in a local editor for manual browser acceptance and do not share it. Script reports always specify `ui_verified=false`; actual UI acceptance must be recorded separately. The existing 64-token real-model smoke test, which permits controlled quality rejection, does not replace this acceptance procedure.

Existing measurement keys and numeric units are retained; diagnostic and deployment status fields are added. Unavailable `own_usage_bytes`, `container_memory`, `cold_start`, and individual cgroup measurements use the stable English value `not_measured`. `pod_metrics` is `available` or `not_measured`; the latter means Pod metrics could not be read, while separately reported Docker node measurements are still used. A missing measurement is not zero or a pass. Boolean acceptance flags remain false until the corresponding checks pass; checks not run must be described as not executed. Historical logs and acceptance reports are not rewritten. Each `up` that passes mandatory checks starts a new `startup.json` attempt before deployment, replacing any previous attempt's readiness claim. It records `attempt_id`, `started_at`, target identity, the complete `preflight` report, and `diagnostic_warnings`.

The nested `preflight.status` is `passed` or `failed`; `preflight.blockers` retains the reasons that make **doctor** fail, not mandatory deployment blockers. Per-read `measurements` use `measured` or `not_measured`, with a safe reason for unavailable data. A successful deployment after warnings still preserves `preflight.status=failed`.

Deployment `status` is `in_progress`, `ready`, `failed`, or `interrupted`. `stage` and optional `component` identify progress or failure, for example `image_build`, `image_load`, `dependency_initialization`, `application_apply`, or `backend_readiness`. `application_ready` becomes true only after all deployment steps and readiness waits succeed; `review_completed` and `ui_verified` remain false. A failed retry replaces an earlier success report. If saving the attempt report fails, the command exits nonzero and explicitly warns that the old report may be stale. An abrupt process kill may leave `in_progress`, which is not success.

## Common failures and handling

| Symptom | Action |
| --- | --- |
| No running cluster, or profile stopped | Start minikube manually and retry. The script never starts it for you. |
| Multiple running profiles | Use the same `--profile NAME` for every command; add `--minikube-home` when needed. |
| API unavailable or certificate error | Check the selected profile's status and certificates. Deployment stops with no fallback to another context. |
| Excessive client/server version skew | `doctor` fails; `up` warns and attempts deployment. Select a compatible kubectl if actual API operations fail. |
| Missing metrics or disk/memory measurements | Read `measurements` and `not_measured` values. `doctor` fails; `up` continues with an explicit warning without assuming spare capacity. |
| Insufficient host/Docker/node resources or disk budget | `doctor` fails; `up` warns and attempts deployment. Inspect the figures and any actual failure stage. Do not delete workloads or lower the backend's 6 GiB limit to make diagnostics pass. |
| CNI lacks policy support | Record that isolation is not enforced and decide whether the environment is suitable for the demo. The script does not install components. |
| Unsupported StorageClass, Pending PVC, or permission failure | Check the existing minikube-hostpath class, Bound PVCs, init containers, backend UID 10001, and DynamoDB UID 1000 / data group 10001. Do not recreate PVCs or recursively chown user data. |
| Architecture/wheel incompatibility | Check native architecture at all three layers, CPU wheels, and locked dependencies. No silent amd64 emulation is allowed. |
| DynamoDB cannot access `DynamoDBLocal.jar` | Verify the actual image user, working directory, parent traversal permissions, and effective Pod/container identity. For the inspected 3.1.0 arm64 image, use UID 1000; an absolute path does not fix a `0700` parent-directory denial. Do not run the main container as root. |
| Download/model-loading timeout | Check disk, DNS, outbound HTTPS, and Hugging Face/CDN connectivity. Keep partial caches and adjust cold-timeout if appropriate; do not substitute another model. |
| BF16/OOM/inference timeout or quality rejection | Preserve the actual error code, resource figures, and evidence. Do not weaken prompts or quality gates, or fall back automatically to float32, which requires a separate budget for increased model and cluster memory. |
| 403 or port in use | Use the saved `http://localhost:PORT` and log in again to obtain CSRF. Change the port through up to synchronize configuration. Stop your own port forward before acceptance. |
| Ownership/legacy-state conflict or missing Secret key | Stop deployment and inspect manually. Do not delete state, adopt resources, or regenerate the signing value. |

`logs` applies an additional filter to structured backend safety logs. For kubectl diagnostics, use the kubeconfig in the target directory printed by the script and explicitly specify context and namespace. Do not export plaintext Secrets or share complete application logs. This workflow does not run AWS/Terraform/EKS operations or introduce cloud observability components.

### Loopback binding and port-forward reconstruction

`verify` closes its first owned port-forward before the controlled workload restart,
then creates a new one on the **same saved port** for persistence acceptance. It
retains the canonical Origin, Cookie and CSRF behavior. A probe is not a reservation
and is never evidence that forwarding has started.

The loopback probe uses `SO_REUSEADDR`, followed by both `bind()` and `listen()`.
No `SO_REUSEPORT`, listener adoption, or termination of unknown processes is used.

Probe failures retain only numeric/symbolic errno and a stable English category:
`address_in_use` for EADDRINUSE, `permission_denied` for EACCES/EPERM, and `bind_failed`
for other socket errors. Local execution policies can prohibit binding even when
no listener exists; this must not be described as an occupied port. Check permissions
in the actual execution environment as well as listener state. Raw exception text
and subprocess output are withheld.

Forwarding requires an exact listener announcement from the newly created child,
a successful loopback connection, and a live child process. Startup has a 10-second
announcement deadline and a one-second connection timeout, with no automatic retry.
The output reader uses bounded chunks so an unterminated output line cannot block
the deadline. Probe/start races, process launch failure, missing announcements,
unreachable listeners and unexpected process exits are failures. Recognized child
bind errors have fixed classifications; unrecognized output remains withheld and
no child errno is invented. Cleanup targets only this invocation's process, waits
up to five seconds after termination, then up to five seconds after killing that
same child. The interactive `port-forward` command uses the same checks and cleanup.

## Incomplete model cache and startup diagnostics

A download progress bar reaching 100% does not establish a complete snapshot. The backend
validates the cached `Qwen/Qwen3-1.7B` snapshot at revision
`70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` before reusing it and again after a download
returns. An indexed snapshot must contain every referenced shard, with readable, structurally
valid safetensors metadata and the exact tensor names assigned by the index. Missing files,
broken links, unreadable files, invalid indexes, truncated weights, and mismatched tensor maps
are rejected. Validation reads metadata without materializing model tensors. The existing
cache-reuse checks in diagnostics and verification use the same validator.

A complete cache is reused offline. An incomplete cache triggers one official downloader call
using the same persistent cache, without `force_download`; existing blobs and resumable downloads
remain available. If downloading or subsequent validation fails, startup fails. There is no
application-level retry loop and no fallback to another model, revision, dtype, or inference mode.
Never rename an `.incomplete` file to a weight file or link an unverified blob into a snapshot.

`logs --profile NAME` includes `model_cache_incomplete`, `startup_stage_started`,
`startup_stage_completed`, and `startup_stage_failed` events. Bounded `stage` values distinguish
`cache_lookup`, `model_download`, `cache_validation`, `tokenizer_load`, `weights_load`,
`cpu_placement`, and `startup_generation`. Coordinator failures also distinguish initial storage,
queue recovery, and `post_model_storage`. Diagnostics include exception type, numeric `errno` or
HTTP status when available, and a stable cache reason. Exception messages, download URLs, tokens,
credentials, source, prompts, and model text are not attached to these events. The public
readiness contract remains HTTP 503 with `startup_or_storage_failure`; internal diagnostics are
not exposed through the browser API.

For a controlled cache repair, first verify the target, ownership chain, PVC, current Pod identity,
startup deadline, and an idle queue. If a long download needs a temporary Pod, pause only the owned
backend after checking the queue, use an explicitly owned non-root temporary Pod with the existing
model PVC, and do not load a second model. Keep it outside ready Service endpoints. Always clean
up that exact temporary resource and restore the backend replica count, including on failure.
Use the official downloader for the pinned missing file, verify it against the official revision's
size/hash and index, and preserve existing valid shards. Do not delete PVCs, reset history, rotate
Secrets, or redeploy DynamoDB/frontend for a model-cache repair.

After updating only the backend image, require complete shards, a completed startup generation
check, the `model_ready` event, HTTP 200 from `/health/ready`, and Pod Ready. These establish backend
startup only; a real completed review and browser acceptance remain separate checks.

## Deployment state and interrupted-attempt recovery

Ownership is established before application deployment finishes. A partial `owner.json` is valid
ownership evidence, but is **not** a completed deployment or an acceptance result. Do not delete it,
regenerate ownership annotations, or populate missing expected-image fields from running Pods.

The target-specific state now separates four records:

- `owner.json` retains source information and home/profile/cluster identity, namespace UID and the ownership marker.
  Compatibility fields for port, architecture, StorageClass and both application builds are updated
  after readiness; partial repair records may contain only some of them.
- `plan.json` records confirmed port/storage/architecture choices, source fingerprints, unique image
  tags and Docker build IDs as they become available. Loaded CRI image IDs are added only after
  comparing their architecture, configuration and filesystem layers with the exact local build.
  Its status remains `planned` and `application_ready=false`.
- `deployment.json` records a completed `ready` deployment only after dependency initialization and
  all application readiness waits pass. It must match the current successful `startup.json` attempt,
  the plan, and ownership/namespace/cluster identity. A new attempt first invalidates deployment and
  acceptance success. Failures/interruption are recorded separately from diagnostic warnings.
- `verification.json` records `in_progress`, `failed`, `interrupted`, `partial` or `passed`, with
  independent `review_completed`, `persistence_verified`, `api_acceptance_passed` and `ui_verified`
  fields. Readiness never implies a completed review or browser acceptance.

Previous deployment/acceptance reports are retained under private `attempts/` directories. Account
credential files and review bodies are not copied into that history. The old backend-readiness
failure is historical evidence and must not be edited into a successful deployment. Consumers must
check the matching deployment attempt and current statuses, rather than treating an old report's
success flag as current evidence.

`verify` validates field presence and types, both frontend/backend image records, unique tags,
Docker/CRI image IDs, source fingerprints and the current Pod image IDs before creating acceptance
accounts, submitting reviews or restarting workloads. Missing/corrupt/partial records fail with an
English recovery message, without dumping state or credentials. Port, architecture and storage are
never guessed during verification. `port-forward` also requires a completed deployment record.

To recover an interrupted deployment, use the selected existing cluster:

```bash
scripts/minikube_demo.sh up --profile minikube
scripts/minikube_demo.sh verify --profile minikube
```

`up` can reuse confirmed port and StorageClass choices from a matching interrupted plan, but still
runs target/ownership/native-architecture/storage checks and records a new attempt. It rebuilds or
reuses images only with matching build evidence, loads unique tags, checks the queue before and
after pausing ingress, and preserves the existing signing Secret and three PVCs. A failed repeat
update restores the owned application's desired replica counts; restoration does not mark that
attempt successful. A separate `verify` is required after the deployment actually completes.

Secret ownership checks project metadata only. The pre-existing signing-key validation computes
only its decoded length inside kubectl; Secret values are not returned to the deployment process
or written to logs/state. If an operator's access policy prohibits even this internal validation,
`up` cannot proceed under that policy; do not remove the check or claim deployment succeeded.


## Undeploy and recovery

The cluster lifecycle remains user-managed. This script cannot intercept a user's
manual minikube stop. To remove this application's resources first, finish the desired
`undeploy` successfully **before** stopping the cluster yourself. Other projects are
outside the deletion scope. `up` never runs cleanup automatically.

First deployment into an already running cluster:

```bash
scripts/minikube_demo.sh up --profile minikube
```

Default teardown removes only explicitly owned Deployments, Services, ConfigMaps,
ServiceAccounts and NetworkPolicies. Kubernetes controller dependents must match their
ownership chain. Each resource is listed in the scope/report. All three PVCs
(`review-history`, `review-model-cache`, `review-dynamodb`), `review-secrets`, saved
account credentials, the namespace and ownership/recovery records are retained:

```bash
scripts/minikube_demo.sh undeploy --profile minikube
scripts/minikube_demo.sh up --profile minikube
```

The second command reuses accounts, signing material, history and model cache. It still
validates ownership, builds current inputs and records a new deployment attempt.

For deliberate deletion of this application's persistent data, both flags are required:

```bash
scripts/minikube_demo.sh undeploy --profile minikube \
  --purge-data --confirm-data-loss local-review-demo
scripts/minikube_demo.sh up --profile minikube
```

This deletes the owned PVCs (model cache, SQLite history/queue and DynamoDB accounts),
signing Secret and the shared saved acceptance-account file, in addition to runtime
resources. The following `up` starts fresh application data. Normal `undeploy`
**always retains the namespace and its ownership marker**, including after purge. The
separate [lost-state recovery command](../operations/minikube-lost-state-recovery.md) can
delete a fully verified namespace only after explicit data-abandonment confirmation. Unknown resources are listed and retained,
never deleted by a broad label selector. Unknown Pods block data purge because their
volume/Secret use cannot be assumed safe. Retaining the namespace is intentional: a
purge is not permission to erase unrecognized or controller-created namespace content.
Original migration sources/backups remain private historical files; purge does not
search other checkouts to erase their copies of old credentials.

Before any workload stop, cleanup validates target and resource ownership, readiness
and the global queue. It pauses the owned frontend with UID/resourceVersion tests,
waits for its Pods to disappear, then checks again. A bounded helper in the existing
backend acquires a SQLite `BEGIN IMMEDIATE` reservation, verifies zero queued/running
jobs and rejects draining/unhealthy workers. Under that reservation the readiness
handler must report `storage_unavailable` from its write probe, not a draining/startup
state. The reservation prevents a racing direct submission from committing a new job
until the backend is gone; no second model process or inference is started. If those
facts cannot be measured, cleanup stops. This temporary write reservation is released
on completion/error and does not change stored review content.

Every explicit deletion rechecks namespace identity and resource ownership and sends
API UID/resourceVersion preconditions. Controller deletion uses foreground propagation
before volume deletion. Waits are bounded (`--delete-timeout`, default 120 seconds;
backend fencing/shutdown is capped at 120 seconds). Finalizers are never stripped.
Only the helper process created by the current operation is cleaned up.

`undeployment.json` records `in_progress`, `failed`, `interrupted`, `undeployed` or
`purged`, the stage, individual results, retained/unknown resources and remaining UID
identities. Deployment/acceptance claims are invalidated and their prior reports are
archived before cleanup starts. No failed cleanup records application or acceptance
success. Repeated cleanup reconciles absence; it does not delete a same-name replacement.

If cleanup fails, inspect its safe error and `remaining` list, resolve the API/finalizer
issue, then repeat the **same** command and data policy. Do not remove state or finalizers
merely to force success. A stopped/missing backend with retained history but no valid
idle-shutdown proof fails closed: restore the owned application using `up`, wait for
existing jobs/draining to finish, then retry `undeploy`. A crash between stopping the
backend and recording its completed idle shutdown also takes this conservative path.
A new `up` invalidates any earlier cleanup's idle proof. Ownership conflicts require
trusted state import or manual investigation; neither up nor undeploy adopts them.

These commands are operational instructions, not evidence that deployment, cleanup,
real inference, persistence or browser acceptance has been performed. Run a separately
authorized `verify --profile minikube` after successful deployment to establish its
actual API/persistence results; browser acceptance remains separate.

When every trusted state copy is lost, do not delete or fabricate `owner.json`. Use the
[read-only lost-state recovery preview](../operations/minikube-lost-state-recovery.md),
then decide whether to explicitly abandon all data. Recovery is separate from normal
undeploy and never automatically follows a failed up.
