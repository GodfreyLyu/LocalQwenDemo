# Local observability

[Documentation index](../README.md)

The local service emits content-free JSON diagnostics. It installs no external collector,
metrics backend, canary or alert infrastructure. Use the [local runbook](../operations/observability.md)
and record measured, unavailable and inferred facts separately.

## Safe application log contract

Every record includes `timestamp` (UTC ISO-8601 with milliseconds), `level`, `service=review-backend`, `environment`, and fixed `event`. `release_sha` is optional and appears only when `RELEASE_SHA` contains 7–64 lowercase hexadecimal characters. Omit unavailable build SHAs; never fabricate them.

The formatter may add only these fields:

| Field | Contract |
| --- | --- |
| `request_id`, `review_id` | Opaque correlation IDs; never metric dimensions |
| `status` | Integer HTTP status |
| `method` | Fixed HTTP verb or `OTHER` |
| `route` | Allowlisted FastAPI route template only; an unknown route is omitted |
| `duration_ms`, `queue_wait_ms` | Non-negative integer milliseconds |
| `queue_depth`, `generated_tokens`, `output_token_limit` | Bounded numeric counters |
| `error_code`, `outcome` | Fixed enums; unknown values become a safe fallback |
| `validation_reason` | One of the fixed validation enums; an unknown value is omitted |
| generation detail maps | Integer/boolean values under the three fixed section names only |

HTTP duration and model generation duration use monotonic clocks. Queue wait and total
review duration can span process restarts. They are calculated from stored UTC
timestamps, with negative values clamped to zero. `X-Request-ID` remains on responses.
Lifecycle events are `review_submitted`, `review_started`, `review_finished`, and
`queue_rejected`; queue depth uses local read-only SQLite counts and never copies source
data.

Never log source, prompt, generated/rejected model text, token IDs, seed, source hash, raw path/URL/query, request or response body, login/user identity, password, cookie, session/CSRF token, secret value, environment dump, or raw exception string. Uvicorn and frontend access logging remain disabled.

## Inference and startup measurement boundaries

Startup diagnostics distinguish Ollama capability/context verification,
startup generation validation and final storage checks. Safe
stage/error-type/status/errno fields never expose raw exception text or URLs.

Generation metrics record generation and time to first content for each fixed section,
plus input and output token counts. Preparation and PyTorch thread fields are null.
Time to first content is included in generation duration; do not add it again. Missing
sections remain unmeasured. Ollama placement is observed through `/api/ps` at startup
or the last successful review.

To measure inference resource use, collect counters from the Ollama container. Backend
counters measure the API, queue, and HTTP client, not the model server. For a separately
authorized single review, bind cgroup measurements to the same Pod UID,
container ID/start time and image. Divide the change in CPU usage by elapsed wall time
to obtain average cores used. Divide CPU seconds by generated tokens to measure CPU cost
per token for that request. Cumulative throttling ratios and `throttled_usec` are not
wall-clock time lost. Compare host swap, Docker/node contention and memory events over
the same interval. Missing metrics stay `not_measured`. Do not run new inference, change
parameters or restart merely to inspect logs.

## Evidence and retention

Readiness does not establish review completion or browser acceptance. Record failures
and partial measurements with target/runtime identities and limitations. Kubernetes/host
log retention is operator-controlled; the project makes no fixed retention guarantee.
The historical [A/B measurements](../reports/README.md) are preserved observations,
not proof of current performance, causal isolation or complete product acceptance.
