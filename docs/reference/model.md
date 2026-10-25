# Model configuration

[Documentation index](../README.md)

## Ownership

The generic [inference service](../../inference-service) selects and pins one model,
supervises Ollama and enforces execution policy. The backend selects review prompts,
section budgets and output validation. See the [Thrift guide](../guides/apache-thrift.md)
for the full request flow, cancellation guarantees and independent startup commands.

The maintained deployment pins `qwen3:1.7b`, Q4_K_M, at
`sha256:8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7`.
It retains the existing model PVC, ARM64 krunkit Vulkan resource and Mesa ICD.
Startup checks native version (at least 0.24.0), installed digest, completion
capability and context capacity, then performs a generic warmup and verifies full
GPU residency. A missing model may be pulled only when explicitly configured;
a different installed revision is never silently replaced.

## Backend configuration

| Setting | Meaning |
| --- | --- |
| `THRIFT_HOST` / `THRIFT_PORT` | Default cluster endpoint `review-ollama.local-inference.svc.cluster.local:9091`; local `.env.example` uses loopback. |
| `THRIFT_SOCKET_TIMEOUT_SECONDS` | Two-second short RPC timeout; independent of generation execution. |
| `THRIFT_STARTUP_TIMEOUT_SECONDS` | Maximum backend handshake wait; default 3600 seconds. |
| `THRIFT_RECOVERY_SECONDS` | Reconnect/poll budget after a lost response or cancellation; default 20 seconds. |
| `EXPECTED_MODEL_ID` / `EXPECTED_MODEL_REVISION` | Optional application identity assertions; deployment pins the selected model. |
| `MODEL_MAX_OUTPUT_TOKENS` | Review-wide output budget, 384 tokens: Summary 72, Findings 176, Suggestions 136. |
| `MODEL_TEMPERATURE` / `MODEL_TOP_P` | Optional application overrides; omitted means the service default. |
| `INFERENCE_TIMEOUT_SECONDS` | Complete review budget, 300 seconds, shared across all three generic generations. |

The backend has no native URL, model context, keep-alive, concurrency, model-pull or
GPU configuration. It discovers service limits/defaults through the handshake.
Service policy can change without reproducing a full configuration in the client.

## Service configuration

The complete typed configuration is in
[`llm_service/config.py`](../../inference-service/src/llm_service/config.py).
The inference chart maps `model` and `generation` values into its own ConfigMap.

| Setting | Default / behavior |
| --- | --- |
| `MODEL_NAME` / `MODEL_DIGEST` | Required pinned identity for execution. |
| `MODEL_CONTEXT` | 4096, must cover input and maximum output budgets. |
| `MAX_INPUT_TOKENS` / `MAX_OUTPUT_TOKENS` | 2048 / 512. Input count comes from native terminal metadata, after admission. |
| `DEFAULT_OUTPUT_TOKENS` | 128 when omitted by an application. |
| `TEMPERATURE` / `TOP_P` | 0.7 / 0.8. |
| `MODEL_THREADS` | Two CPU threads; Vulkan still performs model execution. |
| `REQUIRE_GPU` | True; explicitly disable for CPU development. |
| `MODEL_PULL_IF_MISSING` | False in host configuration; true in Helm for initial installation. |
| `PENDING_GENERATIONS` | Eight pending, one active. |
| `RETAINED_RESULTS` / `RESULT_TTL_SECONDS` | 128 entries, terminal results retained for 600 seconds. |
| `MAX_DEADLINE_SECONDS` | 300, including service queue time. |

Ollama owns tokenization and chat templates. The adapter sends `think=false`,
`truncate=false`, `shift=false`, a fixed context and bounded prediction count.
It rejects thinking/tool output, malformed streams, incomplete terminal output,
wrong identity, invalid counts, over-budget input/output and oversized content.
Native errors are mapped to fixed generic error codes without forwarding content.

Character limits bound admission memory; exact input token enforcement occurs
using native metadata after execution. Therefore an accepted request can fail
`input_limit`. No local tokenizer estimate is claimed. Native context protection
prevents silent truncation, but the configured input token ceiling can be detected
only after the native request completes. This is a deliberate limitation.

## Application review behavior

Every section gets the original source and language hint; generated text is never
used as a later section's input. The backend derives stable nonnegative signed
64-bit seeds from model revision, language, section and source, without logging
those inputs or retaining the derived hash. Section normalization and the limited
unfinished-tail trimming policy remain backend logic.

The backend requires Summary / Findings / Suggestions in order, nonempty bodies
and a limited connection to source identifiers. A capped unfinished section can
still fail the review. These checks do not establish semantic correctness, identify
all bugs, prove complete reasoning or replace human review.

Per-section metrics are reconstructed from generic usage/timing metadata. Backend
RSS measures the client/application only, not model weights or GPU memory.

## Change the model

Install the new model separately, obtain and review its manifest digest, then change
the **service's** `model.name`, `model.digest`, context and generation limits. Keep
the application model assertions in sync if enabled; review prompts remain in the
backend. A service restart creates a new instance ID and loses retained results,
so drain before changing policy or model. Alternate models can obey the generic
protocol while failing application review quality checks; evaluate both.

For managed deployments, follow the [migration/rollback procedure](../operations/inference-migration.md)
and preserve release ownership and PVCs. There is no bootstrap-model versus
application-model split or public native HTTP endpoint in the new architecture.

## Verification

The service tests cover native translation with mocked HTTP, process ownership with
real children, and TCP Thrift with controlled model doubles. The Java test uses a
real compiled generated client. These tests do not load model weights.

With a running service, opt into real review and FastAPI persistence checks:

```bash
RUN_REAL_MODEL=1 THRIFT_HOST=127.0.0.1 backend/.venv/bin/pytest backend/tests/inference/test_real_model.py
```

For quality evaluation and manual judgments, see [model evaluation](../testing/model-evaluation.md).
Historical Transformers measurements remain historical evidence and do not measure
this implementation.
