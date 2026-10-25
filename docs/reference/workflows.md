# Application and release workflows

[Documentation index](../README.md) · [Architecture](architecture.md)

LocalQwenDemo separates accepting a review, executing it, and displaying its saved
result. These diagrams follow the implemented HTTP routes, SQLite transactions,
coordinator and release workflows. Deployment numbers below use the maintained
Minikube values; Python development defaults can differ.

For a project presentation, start with the [system layout](architecture.md#system-layout),
then show the [review sequence](#review-from-submission-to-history) and
[release workflow](#release-deployment-and-rollback). The other views explain the
security, admission and recovery rules behind those three views.

| Workflow | Main question |
| --- | --- |
| [Authentication](#authentication-and-session-ownership) | How does a request acquire its account identity? |
| [Review sequence](#review-from-submission-to-history) | What happens after a user submits code? |
| [Admission](#admission-and-idempotency) | How are duplicates and queue limits handled? |
| [Generation](#model-validation-and-generation) | How does Ollama produce a validated review? |
| [Job lifecycle](#durable-job-lifecycle) | What survives a restart, and what is retried? |
| [Service lifecycle](#service-startup-timeout-and-recovery) | When is the backend ready, draining or unhealthy? |
| [Release and rollback](#release-deployment-and-rollback) | How does source become an approved deployment? |
| [Model evaluation](../testing/model-evaluation.md#evaluation-flow) | How are synthetic reviews measured and assessed? |

## Authentication and session ownership

The browser talks to FastAPI through the same-origin Nginx proxy, omitted from
sequence diagrams for space. Solid arrows are requests; dashed arrows are replies.
The diagram shows successful registration and login. Invalid credentials, Origin
checks, rate limits or storage failures stop the request before a successful response.

```mermaid
sequenceDiagram
    accTitle: Register or log in and create a server-side session
    accDescr: FastAPI checks Origin and rate limits, creates or verifies an Argon2id account in DynamoDB Local, then replaces the browser session in SQLite. The browser receives a signed HttpOnly cookie and a CSRF token.
    participant Browser
    participant API as FastAPI
    participant Accounts as DynamoDB Local
    participant SQLite
    Browser->>API: POST /api/v1/auth/register or /login
    API->>API: Check Origin and auth rate limits
    alt Register
        API->>Accounts: Create account with Argon2id hash
        Accounts-->>API: New account and opaque user ID
    else Log in
        API->>Accounts: Read account by normalized login ID
        Accounts-->>API: Account and password hash
        API->>API: Verify password and enabled account
    end
    API->>SQLite: Delete old session if cookie present
    API->>SQLite: Save token hash, owner, CSRF and expiry
    SQLite-->>API: Session committed
    API-->>Browser: 201 or 200, signed cookie and session JSON
```

The cookie contains an opaque random token plus an HMAC signature. It is HttpOnly
and SameSite Strict; the maintained localhost HTTP profile sets `Secure=false`.
Only a SHA-256 hash of the cookie is stored. The browser keeps the returned CSRF
token in React state and sends it as `X-CSRF-Token` on protected mutations.

Each protected request verifies the signature, unexpired SQLite session and current
enabled DynamoDB account. Mutations also require the exact allowed Origin and CSRF
token. The session supplies `user_id` for history and detail queries; a different
owner's review returns 404. Logout deletes the server-side session and clears the
cookie. Registration and session creation do not share a transaction: an account
can remain if session persistence fails, allowing a later login.

Sources: [auth routes](../../backend/app/api/routes/auth.py),
[session rules](../../backend/app/api/auth.py), [account adapter](../../backend/app/persistence/users.py).
See [security](security.md) for the full policy.

## Review from submission to history

A 202 response confirms durable acceptance, not a completed review. Once the insert
commits, the coordinator can claim it independently of the HTTP response. Browser
polling and background execution overlap; the diagram shows them as parallel paths.
The Worker lane combines the coordinator and its sole executor. Submission uses
`POST /api/v1/reviews`; polling uses `GET /api/v1/reviews/{review_id}`.

```mermaid
sequenceDiagram
    accTitle: Durable asynchronous review from submission to saved result
    accDescr: FastAPI commits the job before returning 202. The browser polls owner-scoped detail while the coordinator independently claims the job, calls Ollama serially and persists a terminal result. The browser renders the saved Markdown after completion.
    participant Browser
    participant API as FastAPI
    participant SQLite
    participant Worker
    participant Ollama
    Browser->>API: POST review, request ID, CSRF
    API->>SQLite: Admit and commit queued job
    SQLite-->>API: Review ID and status
    par Response and polling
        API-->>Browser: 202, review ID and status
        loop Until terminal
            Browser->>API: GET review detail
            API->>SQLite: Read for session owner
            SQLite-->>API: Saved detail
            API-->>Browser: 200, saved detail
        end
    and Background execution
        Worker->>SQLite: Claim job, commit running
        SQLite-->>Worker: Claimed job
        loop Three sections in order
            Worker->>Ollama: Check digest, stream section
            Ollama-->>Worker: Text and token counts
        end
        Worker->>SQLite: Save result or safe error
    end
    Browser->>Browser: Render result or error
```

Polling normally repeats after 1.5 seconds while active, or after 3 seconds following
a retryable polling failure. A page reload reads history and resumes polling an
active job. History lists omit source/result bodies; opening a detail loads them.
The browser receives complete saved results, while streaming stays between the
backend and Ollama. Submitted code is never executed.

If submission delivery is unconfirmed, the UI retains the exact payload and request
ID in memory and lets the user retry explicitly. It does not automatically resubmit.
The admission rules below determine whether that retry returns an existing job.

Sources: [review routes](../../backend/app/api/routes/reviews.py),
[browser workflow](../../frontend/src/App.tsx), [coordinator](../../backend/app/coordinator.py),
[Markdown rendering](../../frontend/src/Markdown.tsx).

## Admission and idempotency

Before the transaction, the HTTP boundary checks the body, schema and authenticated
session. `ReviewService` then checks nonblank/character-bounded input, coordinator
readiness and the per-account submission quota, in that order. These checks also
apply to duplicate requests. The diagram starts after they pass.

```mermaid
flowchart TB
    accTitle: Atomic review admission and duplicate handling
    accDescr: Within one BEGIN IMMEDIATE SQLite transaction, an existing per-user request key returns the same job for identical input or a conflict for changed input. New jobs must fit total and queued capacity and have no other active job for the account before insertion and commit.
    validated["HTTP and service checks passed"]
    subgraph transaction["BEGIN IMMEDIATE"]
        existing{"Request key exists?"}
        sameInput{"Identical input?"}
        reuse["Return existing row"]
        conflict["409 idempotency_conflict"]
        capacity{"Capacity available?"}
        full["429 queue_full"]
        active{"Account has active job?"}
        busy["409 review_active"]
        insert["Insert queued job"]
    end
    accepted["202 after successful commit"]
    validated --> existing
    existing -->|"Yes"| sameInput
    sameInput -->|"Yes"| reuse
    sameInput -->|"No"| conflict
    existing -->|"No"| capacity
    capacity -->|"No"| full
    capacity -->|"Yes"| active
    active -->|"Yes"| busy
    active -->|"No"| insert
    reuse --> accepted
    insert --> accepted
```

Default capacity is eight queued jobs plus one running, with one active job per
account. Both the total-active and queued limits are checked. The unique key is
`(user_id, client_request_id)`; identical retries can return queued, running or
terminal jobs before capacity checks. Changed input under the same key returns 409.
If the client omits the key, the server generates one, so a client must retain a key
to obtain duplicate protection across retries. Storage failure rolls back the
transaction and cannot produce successful acceptance.

Sources: [ReviewService](../../backend/app/review_service.py),
[Store.create_review](../../backend/app/persistence/storage.py). See [API errors](api.md).

## Model validation and generation

At backend startup, `OllamaModel.load` checks the server version, local model digest,
completion capability and context size, then performs a one-token generation probe.
Startup metadata transport/unavailability failures have a 60-second retry budget;
missing models and incompatible metadata fail immediately. The generation probe
runs once. The backend neither downloads a missing model nor selects a fallback.

For a claimed job, the single executor follows this pipeline. All three sections
share one stop event and one 300-second deployment deadline. The total deployment
output budget is 384 tokens, split into 72 / 176 / 136 tokens.

```mermaid
flowchart TB
    accTitle: Three-section Ollama generation and validation
    accDescr: The executor allocates one review-wide budget and checks cancellation, deadline and model identity before each independent section. Ollama streams text and actual token counts. The backend normalizes section bodies, validates the assembled review, then returns it for persistence or fails the job safely.
    budgets["Claimed review | allocate section budgets"]
    guard["Check stop, deadline and digest"]
    chat["Original-source prompt to Ollama /api/chat"]
    section["Check stream, token counts and section body"]
    more{"More sections?"}
    finalChecks["Recheck digest and observe device"]
    validate["Assemble and validate full review"]
    result["Return complete Markdown"]
    failed["Return safe job error"]
    budgets --> guard --> chat --> section --> more
    more -->|"Yes"| guard
    more -->|"No"| finalChecks
    finalChecks --> validate --> result
    guard -.->|"Stopped, expired or mismatch"| failed
    chat -.->|"API error"| failed
    section -.->|"Invalid or over budget"| failed
    finalChecks -.->|"API or identity error"| failed
    validate -.->|"Invalid review"| failed
```

Each prompt repeats the original source and language; generated sections are not
fed into later prompts. Ollama owns tokenization and the model's native template.
Requests disable thinking, truncation and context shifting. Exact input-token
validation happens in the worker using Ollama's terminal counts, after acceptance;
it is not an admission-time estimate. Unexpected thinking/tool output, incomplete
streams, excess tokens and invalid review structure fail the whole job.

The adapter only removes an unfinished suffix under the documented capped-section
rule. It publishes no partial review. Successful persistence records the selected
model tag and actual digest. Output validation checks format and a limited source
connection; a human still needs to assess the findings.

Sources: [Ollama adapter](../../backend/app/inference/ollama.py),
[prompts](../../backend/app/inference/prompts.py), [budgets](../../backend/app/inference/generation.py),
[output checks](../../backend/app/inference/review_output.py).
See [model configuration](model.md) for defaults and error details.

## Durable job lifecycle

These four states are stored in SQLite. `submitting` is a browser-only state.
Transitions back to `queued` occur during process startup recovery, with the default
`MAX_RETRIES=1`; they are not retries of ordinary inference failures.

```mermaid
stateDiagram-v2
    accTitle: Persisted review states and restart recovery
    accDescr: A committed job starts queued, is claimed as running, then completes or fails. Startup preserves queued work and requeues a stale running job once. A later interruption or reduced recovery capacity fails the job. Completed and failed jobs are terminal.
    [*] --> queued: Accept and commit
    queued --> running: Claim
    queued --> failed: Recovery overflow
    running --> completed: Save review
    running --> failed: Error or retry exhausted
    running --> queued: Restart retry
    completed --> [*]
    failed --> [*]
```

Restart preserves queued jobs. A stale running job with `retry_count < 1` is
requeued and its count incremented; a later interruption fails it as `interrupted`.
Recovery keeps the oldest queued jobs up to the configured capacity, including
requeued interrupted work; excess jobs fail as `queue_recovery_limit`. Completed
and failed jobs remain queryable. A timeout fails the job before executor draining
finishes, and a late result cannot replace that failure. The user can submit a new
job when the service is ready. Retrying the same request key returns the old job.
Recovery is restart-safe for the supported single process, without an exactly-once
inference guarantee or distributed worker leases.

Sources: [Store.recover, claim and finish](../../backend/app/persistence/storage.py),
[Coordinator.process](../../backend/app/coordinator.py).

## Service startup timeout and recovery

Coordinator state describes the backend process, independently of any saved job.
`ready` remains the operational state during normal execution, so other accounts
can queue work. The process never starts a second inference while the first local
executor call is still draining.

```mermaid
stateDiagram-v2
    accTitle: Backend coordinator startup and timeout recovery
    accDescr: The coordinator starts loading, initializes stores, recovers the queue and verifies the model before becoming ready. A coordinator wait timeout enters draining. A returning executor restores readiness within 30 seconds, otherwise the coordinator becomes stuck. Initialization or worker-loop storage errors are fatal states requiring restart.
    state "model_loading" as loading
    state "ready" as readyState
    state "inference_draining" as draining
    state "inference_stuck" as stuck
    state "startup_or_storage_failure" as failure
    [*] --> loading
    loading --> readyState: Startup checks pass
    loading --> failure: Startup error
    readyState --> draining: Timeout
    readyState --> failure: Storage or loop error
    draining --> readyState: Drained
    draining --> stuck: Drain expires
    draining --> failure: Write error
```

Startup order is SQLite initialization → account-store check → queue recovery →
model validation/probe → final writable SQLite check. The account-store stage
retries only allowlisted transport failures, serially, within a 120-second retry
budget; an in-flight SDK call is allowed to finish. In Kubernetes, an init container
first ensures the users table exists. See [startup recovery](../operations/recovery-and-cleanup.md).

| Operational state | Ready when not closing | Live | Consequence |
| --- | --- | --- | --- |
| `model_loading` | No | Yes | Wait for initialization |
| `ready` | Yes, if writable SQLite check passes | Yes | Admit and claim jobs |
| `inference_draining` | No | Yes | Signal stop, wait for local executor exit |
| `inference_stuck` | No | No | Stop coordinator; Kubernetes liveness restart |
| `startup_or_storage_failure` | No | No | Restart/recovery required |

`/health/ready` and `/api/v1/runtime` use the same observation. A failed writable
store check reports `storage_unavailable` without changing the coordinator state;
it is not itself a liveness failure. Runtime observations return HTTP 200, with
`accepting_submissions=false` when unavailable; readiness returns 503. Browser UI
submission gating uses runtime, while actual admission still checks readiness and
transactional limits.

Shutdown sets a separate `closing` flag, closes admission, signals cancellation and
waits for a bounded grace period. Runtime reports `shutting_down`; the last
operational state is retained. A late thread exit cannot recover from `stuck` or
reopen admission while closing. The Pod termination grace period is 60 seconds;
Python cannot forcibly kill the native thread. An adapter-reported timeout that
has already returned can fail a job without entering the coordinator drain path.

Sources: [coordinator](../../backend/app/coordinator.py),
[readiness](../../backend/app/health.py), [account startup check](../../backend/app/persistence/users_startup.py),
[backend Deployment](../../deploy/helm/local-review/templates/deployment-review-backend.yaml).

## Release deployment and rollback

`main` owns source and trusted release tools. `deployment-release` owns approved
snapshots. CI validates configuration without cluster access; approval, deployment
and real business acceptance are separate stages.

```mermaid
flowchart TB
    accTitle: Source to approved snapshot and deployment
    accDescr: A source PR passes main-ci, then merged main passes quality again. Changed images are published to GHCR and an immutable release PR pins their digests. Snapshot checks and manual merge update deployment-release. Tagging and cluster deployment proceed independently. Argo CD or standard Helm deploys the two charts, followed by real business acceptance.
    source["Merge source PR after main-ci"]
    quality["Recheck merged main"]
    images["Build changed images to GHCR"]
    candidate["Snapshot PR with pinned digests"]
    checks["deployment/snapshot"]
    approval["Manual merge | deployment-release"]
    tags["Create deploy/v and source v tags"]
    manager{"Deployment manager?"}
    argo["Argo CD | two Applications"]
    helm["Operator Helm | two Charts"]
    acceptance["Verify services and a saved review"]
    source --> quality --> images --> candidate --> checks --> approval
    approval --> tags
    approval --> manager
    manager -->|"Argo-owned"| argo
    manager -->|"Helm-owned"| helm
    argo --> acceptance
    helm --> acceptance
```

Failed quality or snapshot checks stop promotion. Snapshot validation covers
provenance, Chart rendering, Kubernetes schemas, monitoring rules and dashboards;
it does not execute a model review. Backend/frontend images target AMD64 and ARM64;
the GPU Ollama image targets ARM64. Deployable inputs that have not changed reuse
their approved image digests. A docs-only change can produce no release candidate.

The snapshot pins images and includes both Charts. Choose one manager per deployment.
Argo CD renders Helm templates and reconciles resources; these are not ordinary
Helm-installed releases. The two Applications reconcile independently. Tags record
approved desired state, not successful deployment, and a tagging failure does not
block Argo CD from syncing an already merged snapshot.

For a fresh environment, prepare the cluster, StorageClass, namespaces, GPU platform
and external Secrets, then deploy Ollama before the application. Ollama reuses its
model PVC and, with bootstrap enabled, downloads a missing model and verifies its
pinned digest. Its readiness then checks process/API availability rather than GPU
residency. A separate Helm test or Argo CD PostSync hook proves bootstrap-model GPU
inference. The backend independently probes its selected review model. Complete a
real review through the UI and check saved history after deployment.

An audited rollback uses the same snapshot validation and manual approval, while
recovering the deployment before normal releases resume:

```mermaid
flowchart TB
    accTitle: Audited rollback and return to normal releases
    accDescr: After a deployment failure, operators pause ordinary deployment merges, select a compatible approved tag, create a new rollback candidate with retained image digests, validate and manually merge it, and verify recovery. They then fix main or the environment and explicitly resume ordinary releases. Stored data and external Secrets are retained.
    incident["Failed deployment B"]
    pause["Pause ordinary deployment merges"]
    target["Select compatible approved tag A"]
    candidate["New rollback candidate C"]
    approval["Snapshot checks and manual merge"]
    recover["Deploy C and verify saved review"]
    repair["Repair main or environment"]
    resume["Explicitly resume ordinary releases"]
    incident --> pause --> target --> candidate --> approval --> recover --> repair --> resume
```

The rollback copies the approved snapshot into a **new** candidate based on
the current release head, reuses its exact images without rebuilding, and passes
the same validation and manual merge. Keep Argo auto-sync and main CI running while
ordinary deployment merges are paused. Rollback restores configuration, not PVC
data, Secrets or external platform state. Recheck data compatibility, verify the
recovery, and repair source/environment before explicitly resuming normal releases.

Sources: [quality checks](../../.github/workflows/ci.yml),
[candidate workflow](../../.github/workflows/release-candidate.yml),
[snapshot validation](../../.github/workflows/deployment-validation.yml),
[tag finalization](../../.github/workflows/release-finalize.yml),
[Argo Applications](../../deploy/argocd), [Ollama runtime](../../deploy/images/ollama-vulkan/runtime.py).
Use the [GitOps](../guides/gitops.md), [Helm](../guides/helm-release.md),
[platform](../../infra/local-platform/README.md) and [rollback](../guides/release-rollback.md)
guides for executable steps.

## Maintaining the diagrams

The Mermaid fences in this page and the linked references are the editable sources.
GitHub renders them in place. Keep labels short and put policy details beside each
diagram. Update the relevant diagram and its source links when changing routes,
storage, model execution, health transitions or release ownership. The architecture
page's [implementation map](architecture.md#implementation-map) identifies the
code to inspect. Dated reports describe their recorded revisions rather than the
current design.
