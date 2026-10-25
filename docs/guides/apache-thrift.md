# A reusable LLM service over Apache Thrift

The backend owns the application. The inference service owns execution. Their only
shared Python dependency is the generated [contract](../../contracts/llm.thrift).
A different application can send its own messages without knowing anything about
code reviews, Python source, or Summary / Findings / Suggestions.

## Projects and ownership

| Project | Owns | Does not import |
| --- | --- | --- |
| `backend/` | FastAPI, auth, sessions, SQLite queue/history, DynamoDB accounts, prompts, section orchestration, review validation, Thrift client | `llm_service` |
| `inference-service/` | Ollama child, native loopback HTTP, model pin, defaults, limits, registry, RPC processor, shutdown | `app` or backend modules |
| `contracts/` | IDL, generated Python and Java bindings, tiny Java example | Either application's implementation |

Each application has its own `pyproject.toml`, runtime/development lockfiles,
Dockerfile, entry point and tests. Install `contracts` into each environment, or
build it as a wheel. Neither application needs the other's dependencies to run.

The inference entry point is `llm-service` (`python -m llm_service.runtime`). Its
modules are intentionally small and direct:

- `lifecycle.py` starts, monitors and reaps the native process group. It is derived
  from the former `deploy/images/ollama-vulkan/runtime.py`.
- `ollama.py` validates the pinned model and translates messages into native chat.
- `handler.py` owns the bounded request registry and a single generation worker.
- `server.py` accepts bounded TCP connections and calls the generated processor.
- `runtime.py` connects those lifecycles and serves readiness/liveness.

The backend's `app/inference/rpc/client.py` constructs three sets of messages,
allocates the review output budget, polls generic generations, records per-section
observations, joins headings and validates the resulting review. Those choices are
application behavior, so the service cannot depend on them.

## One request, end to end

1. A Python or Java client calls `getModelInfo` and `getHealth`. Model info supplies
   the actual model revision, supported defaults, limits and a service instance UUID.
2. The application supplies role/content messages, a new request ID, the instance
   UUID, an elapsed generation deadline and optional output tokens, temperature,
   top-p and seed. `startGeneration` admits work and returns quickly.
3. The generated client serializes a binary-protocol Thrift message in a framed
   TCP transport. The generated processor decodes it and calls the handler.
4. The worker passes the messages to Ollama on `127.0.0.1:11434`. Ollama owns chat
   templates/tokenization; thinking, tools, truncation and context shifting are disabled.
5. On completed native output, the service verifies identity, token usage and GPU
   residency. `getGeneration` returns content, model ID/revision, token counts and
   `stop` or `length`. The application decides how to interpret the content.

The wire stack is **TCP → framed transport → strict binary protocol**. Python and
Java use the same stack, field IDs, signed 64-bit seeds and epoch milliseconds.
Frames and strings are limited to 2 MiB; containers to 64 entries. The service
supports up to 32 messages, 64,000 content characters, 128,000 result characters
and 32 concurrent RPC connections by default. Idle connections time out after
three seconds. This is an internal trusted-network API without TLS or RPC auth.

## Execution guarantees and limits

Defaults are service policy, discoverable through `getModelInfo`; clients do not
copy the service's configuration just to connect. Kubernetes keeps the existing
pinned `qwen3:1.7b` revision and full GPU residency requirement. CPU development
must explicitly set `REQUIRE_GPU=false`.

| Concern | Behavior |
| --- | --- |
| RPC timeout | Backend uses two seconds per connection/call; this does not end model execution. |
| Generation deadline | At most 300 seconds by default, measured from admission, including queue time; enforced even if all clients disconnect. |
| Concurrency | Exactly one native generation. Up to eight pending requests. Full admission returns `overloaded`. |
| Retention | At most 128 total live/retained entries. Every terminal result stays for 600 seconds from completion; no early eviction for a new request. Full registry returns `retention_full`. |
| Expiry | `expires_at_ms` is the advertised terminal expiry. Afterwards lookup returns `not_found`; reuse of an expired ID can execute again. Never resubmit an uncertain old operation after expiry. |
| Duplicate ID | Same instance and identical payload return the existing snapshot, including after completion. |
| Conflicting ID | Different messages/options/deadline return `idempotency_conflict`. Explicit defaults and omitted options are distinct payloads. |
| Connection loss | Work continues. Recover with `getGeneration(instance_id, request_id)`. Backend never blindly retries a lost start response. |
| Unknown execution | `not_found`, an instance change, or exhausted reconnect/cancellation reconciliation closes backend admission with `inference_stuck`; operator recovery is required. |
| Restart | Registry is in memory. A new instance UUID fences all old IDs. Results are lost; a new instance cannot assert whether an old operation completed. |
| Cancellation | Queued work cancels immediately. Active work becomes `CANCELLING`; only a terminal snapshot confirms native exit. |

Cancelling an HTTP stream alone does not prove that the runner exited. The service
terminates the Ollama process group, sends SIGKILL to remaining group members,
reaps its child, and starts/validates the pinned model again before returning from
an aborted generation. The single active slot prevents this from cancelling a
second generation. Recovery can take longer than the generation deadline; health
is unready during recovery. Failed recovery closes the service and Kubernetes
restarts it. Native stdout/stderr are suppressed because they may include content.
Service logs contain fixed events/codes, never messages, source or generated text.

This deliberately trades cancellation latency and model reload cost for a clear
completion guarantee. There is no distributed scheduler, durable result registry,
streaming RPC or multi-replica routing. A durable cross-process exactly-once
workflow would need persisted request/result state and restart reconciliation.
The backend's durable review queue can retry interrupted application work after
restart; that is a separate policy and can repeat computation after a crash.

## Startup and shutdown

Startup: own an Ollama child → wait for native HTTP → check version and installed
revision → optionally pull a missing model → check capabilities/context → perform
a generic warmup and GPU check → open Thrift and health listeners → backend
initializes storage, checks accounts, recovers its queue and completes the handshake
→ open HTTP submission admission.

Shutdown: close admission → request cancellation of queued/active work → stop and
reap Ollama and its runner → join the generation worker → close RPC and health
listeners → exit. The backend separately drains its review client and persists
terminal review state. Unexpected Ollama exit terminates the inference service;
a replacement starts with a new instance UUID.

## Run the projects independently

Use Python 3.12 for the backend. The service supports Python 3.12–3.15 (the Fedora
image supplies its own interpreter). Run all following commands from the repo root.

```bash
python3.12 -m venv backend/.venv
```

```bash
backend/.venv/bin/pip install -r backend/requirements-dev.lock
```

```bash
backend/.venv/bin/pip install --no-deps -e contracts -e backend
```

```bash
python3.12 -m venv inference-service/.venv
```

```bash
inference-service/.venv/bin/pip install -r inference-service/requirements-dev.lock
```

```bash
inference-service/.venv/bin/pip install --no-deps -e contracts -e inference-service
```

Copy each project's `.env.example` to its own `.env`, configure signing/database
settings only in the backend, and start the service from `inference-service/`.
Ollama must be on PATH, its model installed, and the selected native port free. The service
starts Ollama itself. `OLLAMA_PORT` defaults to 11434; set another loopback port
when a host Ollama application already uses it. The service fails before startup
if the selected port is occupied. Set `OLLAMA_MODELS` to an existing model directory if needed;
do not start another Ollama against the same native port. The image uses `/models`.

```bash
cd inference-service
```

```bash
.venv/bin/llm-service
```

In another terminal, from `backend/` with DynamoDB Local initialized:

```bash
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --no-proxy-headers
```

From the repo root, a generic client needs only `contracts` and the Thrift runtime:

```bash
backend/.venv/bin/python scripts/thrift_demo.py --prompt 'Translate Hello to French.'
```

```bash
backend/.venv/bin/python scripts/java_thrift.py --request-id java-demo-1
```

The Java script downloads SHA-256-pinned TCP dependencies to `.local/java-thrift`,
compiles the checked-in generated Java sources and the single `Generate.java`, and
runs it. It needs a JDK (Java 21+); no full Java application or Maven project is required.
Use a fresh request ID for new work. The demos intentionally print synthetic output;
the server does not log it.

## Regenerate, test and build

Install Apache Thrift **0.25.0**. Generated files must not be edited by hand.

```bash
python3 scripts/generate_thrift.py
```

```bash
python3 scripts/generate_thrift.py --check
```

```bash
backend/.venv/bin/pytest backend/tests -m 'not real_model and not requires_cluster'
```

```bash
bash scripts/checks/inference.sh
```

The inference gate includes real generated processors/clients over TCP, a Java and
Python shared-ID test, model-double execution tests, mocked native HTTP translation
and real child-process lifecycle tests. These are not real model inference. Opt-in
real review tests require a separately running service:

```bash
RUN_REAL_MODEL=1 THRIFT_HOST=127.0.0.1 backend/.venv/bin/pytest backend/tests/inference/test_real_model.py
```

Both images use repository-root build context; each installs only its project and
the contract. The inference image keeps the pinned Fedora/Ollama/Mesa Vulkan stack.

```bash
docker build -f backend/Dockerfile -t review-backend:local .
```

```bash
docker build --platform linux/arm64 -f inference-service/Dockerfile -t review-inference:local .
```

See [migration and rollback](../operations/inference-migration.md) before deploying.
The existing Helm release is named `review-ollama`; its image now runs both processes
and its Service exposes Thrift 9091 and health 11435. Native HTTP stays on loopback.
