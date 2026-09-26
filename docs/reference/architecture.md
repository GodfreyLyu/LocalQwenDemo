# Architecture

[Documentation index](../README.md)

## Request flow

The [homepage diagrams](../../README.md#architecture) show the application inside minikube
and its host-side management tools. React executes in the browser; Nginx serves its static
bundle and provides the same-origin API proxy. The background coordinator and one-thread
inference executor run within the FastAPI process.

### One review, end to end

1. `RequestGuard` bounds the JSON body and assigns a request ID. FastAPI validates
   `ReviewInput`; the session dependency verifies the signed cookie, account, Origin
   and CSRF token. The user ID comes only from this authenticated session.
2. The review route obtains a typed `ReviewService` dependency. Its `submit` method
   checks blank/character input, coordinator readiness, token bounds and the per-user
   submission rate limit, in that order. These checks also apply to idempotent retries,
   preserving the existing admission policy.
3. `Store.create_review` uses **one `BEGIN IMMEDIATE` transaction** for the existing-key
   lookup, payload conflict check, total/queued capacity checks, active-user check and
   insert. Same-key/same-input retries return the existing row before capacity checks.
   A duplicate with different input returns 409; no service-layer read/check/insert
   sequence is used. Commit completes before the service emits its acceptance log.
4. `CreateReviewResult` carries the row separately from `created` and the transaction's
   queue-depth snapshot. Only newly inserted reviews emit `review_submitted`.
   `ReviewAccepted` exposes three fields and the route returns 202 without waiting
   for inference. A storage failure never produces a successful acceptance response.
5. The coordinator claims a queued review, persists its running state and sends it to
   its sole inference executor. `TransformersModel` prepares and generates the three
   sections. Pure prompt and output rules live in `inference/prompts.py` and `inference/review_output.py`;
   `inference/generation.py` owns sampling/seed/budget rules and safe metrics. One small
   `GenerationContext` shares a stop event, deadline and metrics across the sections.
6. The coordinator persists completion or a safe failure through `Store.finish`.
   The browser polls an owner-scoped detail query. `ReviewDetail` and `HistoryPage`
   allowlist public fields; list entries omit source/result bodies. Timestamps remain
   numeric Unix seconds, IDs remain strings, and nullable fields remain JSON null.

### Types and dependency boundaries

`api/schemas.py` uses Pydantic only at the HTTP boundary: input validation, response
allowlists and the optional OpenAPI schema. `domain.py` uses TypedDict for session,
account, review and history records: they remain ordinary dictionaries and are not
reparsed into domain models at every layer. SQLite/DynamoDB adapters annotate their
known row shapes; these annotations are not runtime database validation.
`CreateReviewResult` and generation metrics/context are small dataclasses because
operation metadata and mutable execution observations are not HTTP payloads.

`create_app` assembles settings, stores, the limiter, model, coordinator and service
for each application instance. `api/dependencies.py` is the only route-facing adapter
from Request/app state to those typed dependencies. The service takes explicit
Python collaborators and has no Request/Response dependency. Simple owner-scoped
history/detail queries call `Store` directly from routes; a forwarding-only service
layer would add no policy. Canonical callers use `app.inference`, `app.persistence` and `app.api`.
The root `app.model` remains an explicit compatibility facade for older evaluator
and operational imports; implementation modules never import through it.

### Shared readiness and rate limits

Both `/health/ready` and `/api/v1/runtime` call the HTTP-independent
`health.check_readiness`. Its immutable result carries the observed status and safe
error code. The health route maps failures to HTTP 503; runtime keeps HTTP 200 for
successful observations of an unavailable service and retains the same diagnostic
error code. Runtime separately presents closing as `shutting_down`. No route calls
another route or creates a throwaway HTTP response to discover its status.

The observation still calls SQLite `ping` only while the coordinator is ready; this
is a writable-store check that also removes expired sessions. Loading, draining and
closing skip that check. The existing last operational status in a shutdown health
response is preserved even though its HTTP status is 503.

`rate_limit.RateLimiter` is shared by authentication and review admission without
pulling HTTP authentication code into the business service. The application factory
still creates one limiter per app. A lock keeps quota checking and recording atomic
across worker threads; rejected attempts do not consume quota. The legacy
`app.auth.RateLimiter` import remains available. Limits, windows, key eviction and
error responses are unchanged.

## Boundaries

The browser edits and displays text. The frontend serves the UI and proxies `/api/*`
and `/health/*` to `review-backend:8000`, preserving normal Cookie, Origin and CSRF
handling. The HTTP boundary validates the session/account, ReviewService applies admission
policy, and Store checks idempotency while atomically persisting a queued review. The HTTP request ends before inference; the
browser polls the persisted status and renders only sanitized completed Markdown.
The cluster is accessed through an explicitly owned loopback forward, not a public ingress.

One Uvicorn process and one background coordinator own a dedicated one-thread inference executor. Each review invokes the executor once to generate Summary, Findings, and Suggestions sequentially. The sections share one 384-token deployment budget split 72/176/136, one deadline, and one stop event; no section output becomes a later model instruction. If an individual section reaches its fixed limit, the backend may delete only its incomplete trailing fragment after the last complete terminator. It never adds a continuation call or alters an earlier complete sentence or list item.

Qwen3's tokenizer chat template is always invoked with the hard `enable_thinking=False` switch, so the application neither requests nor parses chain-of-thought. Each section uses the pinned model's explicit non-thinking sampling parameters and a stable SHA-256-derived seed inside an isolated CPU RNG context. Exiting the context restores the process RNG state, and neither seed nor source-derived hash is logged or persisted. Repeatability is scoped to the same dependency, CPU, and runtime stack.

DynamoDB and SQLite operations run in FastAPI's thread pool or `asyncio.to_thread`; model execution never runs on the main event loop. One backend replica and the `Recreate` rollout strategy preserve this ownership. There is no HPA, distributed queue, conversational store, external LLM, or code execution path.

## Storage

| PVC                  | Mounted by                       | Data / configured size                                                |
| -------------------- | -------------------------------- | --------------------------------------------------------------------- |
| `review-history`     | Backend at `/data`               | `reviews.sqlite3`: queue, review bodies, history and sessions; 10 GiB |
| `review-model-cache` | Backend at `/models/huggingface` | Pinned snapshot, tokenizer and download cache; 12 GiB                 |
| `review-dynamodb`    | DynamoDB Local at `/data`        | Account records and password hashes; 1 GiB                            |

The signing key is a separate `review-secrets` Kubernetes Secret. Host-side deployment
state is also separate: it records target/build/acceptance evidence and private credentials,
not application history. Default undeploy retains all three PVCs and signing material.
Hostpath capacity declarations are not disk reservations or backups.

`backend/app/persistence/storage.py` provides parameterized SQLite queries, explicit transactions, and schema versioning through `PRAGMA user_version`. Version 1 is created atomically; unknown versions fail startup. Future schema changes must introduce explicit ordered migrations before increasing the supported version. SQLite uses WAL, `synchronous=FULL`, and a five-second busy timeout. Each operation creates and closes its own connection.

Reviews store review/user/client IDs, language, source, result, state, error code/message, model ID/revision, retry count, and creation/update timestamps. Indexes cover user/reverse time ordering, status/creation ordering, and a unique `(user_id, client_request_id)` key. Transactions serialize duplicate submissions and capacity checks. A duplicate key with identical input returns the existing job, even after completion; a changed payload returns 409.

History listings are cursor-paginated and omit source/result text. Detail access and pagination cursors always derive user identity from the authenticated session. Another user's ID returns 404. User input never supplies authoritative `user_id` values.

Sessions are signed opaque random tokens; only a SHA-256 token hash, user identity, CSRF token, and expiry are stored in SQLite. Logout deletes the session, so replaying a copied cookie fails. DynamoDB stores account identifiers, Argon2id hashes, creation timestamps, disabled flags, and opaque user IDs. It does not store review history.

## Queue and restart policy

By default, the queue allows eight queued jobs plus one running job, with at most one active job per account. Admission and status transitions are persisted before HTTP acceptance or inference. History writes fail closed: no success is returned when storage fails.

Before queue recovery, startup account-store checks retry only explicitly allowed transport failures within a 120-second budget, using serial single-attempt SDK calls and asynchronous capped backoff. Readiness stays false throughout. See [startup retry boundaries](../operations/recovery-and-cleanup.md#temporary-account-store-connection-failures). This does not retry other initialization stages or inference.

On process restart, queued jobs are recovered. Stale running jobs are requeued once, incrementing `retry_count`. A second interruption fails them as `interrupted`. A reduced queue capacity preserves the oldest jobs and fails excess recovered work. Ordinary inference failures, empty or invalid output, and timeouts are not automatically retried; the user may make a new submission.

A timed-out inference is marked failed, receives a cooperative stopping signal, and makes readiness false until its thread exits. The three section generations do not receive separate timeout windows: the 300-second minikube inference deadline covers the complete review, and stop or deadline expiry prevents another section from starting. No second inference starts during draining.

If the inference thread exits within the 30-second draining window, readiness recovers, liveness remains healthy, and the Pod is not restarted. Only draining that exceeds 30 seconds enters `inference_stuck`, makes liveness false, stops the coordinator, and causes Kubernetes to restart the process. Python cannot forcibly kill a native thread. The 60-second Pod termination grace period bounds shutdown even if native execution becomes stuck. A killed running job is recovered under the documented retry policy.

### Coordinator states and shutdown

`CoordinatorState` replaces separately mutable readiness/liveness flags. State
changes go through `_transition`; `ready` and `live` are read-only properties.
`closing` is an independent lifecycle flag: shutdown can occur during loading,
normal processing or draining without hiding the last operational state.

| State | Ready (while not closing) | Live | Next behavior |
| --- | --- | --- | --- |
| `model_loading` | false | true | Initialize storage/accounts, recover queue, load and validate model |
| `ready` | true | true | Claim and execute jobs serially |
| `inference_draining` | false | true | Wait for the timed-out executor future; never start another job |
| `inference_stuck` | false | false | Stop claiming jobs; late thread exit cannot restore readiness |
| `startup_or_storage_failure` | false | false | Stop the worker loop; restart/recovery is required |

A cooperative or late successful/failed exit during the drain window returns to
`ready`; the persisted timeout remains failed and is not replaced by a late result.
Shutdown sets `closing`, clears readiness through the property, signals the stop
event and waits for a bounded grace period before cancelling the coordinator task.
Loading or draining finishing during shutdown cannot reopen admission. Cancellation
cannot kill native inference; process termination is still the final bound.

## Design tradeoffs and scaling boundary

The implementation deliberately has one Uvicorn process, one coordinator and one
inference executor thread. This bounds model memory and serializes CPU inference;
HTTP handlers and database operations can still execute in worker threads. It is
appropriate for a local demonstration, not a multi-worker deployment guarantee.
`MODEL_INFERENCE_CONCURRENCY=1` is validated, and deployment starts Uvicorn with one
worker and one replica. Increasing Uvicorn workers would instantiate more models,
coordinators and limiters; the in-process constraint would no longer be global.

SQLite keeps queue admission, idempotency, history and session revocation together
with short transactions and no separate broker. WAL supports concurrent readers,
while writes serialize. Its queue survives process restarts, but it provides no
worker lease or distributed execution ownership. DynamoDB Local holds accounts and
Argon2id hashes separately, exercising the DynamoDB API without cloud credentials.
The cost is a second local dependency and no transaction spanning account creation
and SQLite session creation; a successful registration followed by a session-store
failure can leave an account that must log in later.

Multi-process operation would require explicit worker ownership/leases, safe
recovery that cannot requeue another live worker's job, shared admission/rate-limit
policy, model-memory planning, and cross-worker cancellation/health semantics.
Database placement, write contention and durable handoff would also need review.
The existing SQLite transaction prevents duplicate admission; it does **not** make
inference exactly-once or make current startup recovery safe across workers. None
of those distributed capabilities is implemented here.

## Deployment and persistence

`deploy/kustomize/overlays/minikube` is the sole maintained deployment overlay. Its local
base describes hardened application resources. A namespace-scoped signing Secret is
created once by the deployment script and reused. Three independent PVCs hold history/
queue/sessions, model cache and DynamoDB Local accounts. Existing supported hostpath
storage and CNI are inspected, never installed or reconfigured by the scripts.

The minikube helpers verify profile/home/cluster identity, use a private kubeconfig and
shared operation lock, and enforce namespace UID and random ownership markers. Source
fingerprints, unique local image tags and image IDs bind deployment/verification to the
actual checkout. [State import and cleanup](../guides/minikube-demo.md#state-and-ownership-protection)
work across checkout moves. Readiness and acceptance have independent reports.

## Observability flow

The backend emits allowlisted JSON events to stderr. The `logs` command filters these
again for operator inspection. No cloud collector, metrics exporter or automated alarm
service is installed. [The log contract](observability.md) and [local runbook](../operations/observability.md)
define safe correlation, timing boundaries and unknown measurements.

## Frontend task status

The result panel reports submission, queueing, execution and terminal task states separately from service readiness. It does not display a time estimate or progress percentage. Real-model execution includes a brief CPU timing explanation; simulated execution is explicitly labeled. These display choices do not change the 300-second minikube inference timeout or the frozen-request retry used when delivery is uncertain.

## Implementation map

The package layout follows existing responsibilities with one level of grouping:

```text
backend/app/
  main.py, config.py, domain.py, errors.py
  review_service.py, coordinator.py, health.py, rate_limit.py, logging.py, startup.py
  api/
    routes/                   # auth, reviews, health, runtime HTTP endpoints
    auth.py, dependencies.py, schemas.py, middleware.py, http_errors.py
  inference/
    model.py, prompts.py, generation.py, review_output.py, model_cache.py, identity.py
  persistence/
    storage.py, users.py, local_dynamodb.py, users_startup.py
  model.py, auth.py           # explicit legacy exports only
```

The root owns application assembly, business coordination and shared diagnostics.
`api` owns HTTP transport; `inference` owns model execution and pure generation rules;
`persistence` owns local storage and account-store startup checks. `startup.py` stays
at the root because it is shared safe diagnostics, not the account retry mechanism.
No service/repository framework or additional business layer is introduced.


Use these sources when auditing or changing the design:

| Concern                                              | Source                                                                                           |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Application construction and HTTP contracts | [main.py](../../backend/app/main.py), [schemas.py](../../backend/app/api/schemas.py), [routes](../../backend/app/api/routes) |
| Admission policy and typed dependencies | [review_service.py](../../backend/app/review_service.py), [dependencies.py](../../backend/app/api/dependencies.py), [domain.py](../../backend/app/domain.py) |
| HTTP protection and error mapping | [middleware.py](../../backend/app/api/middleware.py), [http_errors.py](../../backend/app/api/http_errors.py) |
| Durable admission, recovery and sessions             | [storage.py](../../backend/app/persistence/storage.py)                                                       |
| Single executor, readiness and cancellation/draining | [coordinator.py](../../backend/app/coordinator.py)                                               |
| Inference and cache checks | [model.py](../../backend/app/inference/model.py), [model_cache.py](../../backend/app/inference/model_cache.py) |
| Prompt, output and generation policy | [prompts.py](../../backend/app/inference/prompts.py), [review_output.py](../../backend/app/inference/review_output.py), [generation.py](../../backend/app/inference/generation.py) |
| Local accounts transport                             | [users.py](../../backend/app/persistence/users.py), [local_dynamodb.py](../../backend/app/persistence/local_dynamodb.py) |
| Deployment composition                               | [Minikube overlay](../../deploy/kustomize/overlays/minikube/kustomization.yaml)                  |

The [script reference](scripts.md) maps shared-state, target, deployment and cleanup modules.
