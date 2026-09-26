# API overview

[Documentation index](../README.md)

Request/response bodies use JSON, except that successful logout returns **204 with no body**. API responses carry `X-Request-ID` and `Cache-Control: no-store`. Source, passwords, cookies, and model responses are never request-log fields. Invalid input errors do not echo Pydantic's input values.

| Endpoint                                   | Success | Purpose                                                                                         |
| ------------------------------------------ | ------- | ----------------------------------------------------------------------------------------------- |
| `POST /api/v1/auth/register`               | 201     | Normalize identifier, conditionally create user, create session                                 |
| `POST /api/v1/auth/login`                  | 200     | Verify Argon2id hash, rotate session                                                            |
| `POST /api/v1/auth/logout`                 | 204     | Revoke session and delete cookie                                                                |
| `GET /api/v1/auth/me`                      | 200     | Current identifier, CSRF token, expiry, character limit                                         |
| `POST /api/v1/reviews`                     | 202     | Atomically accept or return an idempotent review                                                |
| `GET /api/v1/reviews?limit=20&before=UUID` | 200     | Current user's history and `next_cursor`                                                        |
| `GET /api/v1/reviews/{UUID}`               | 200     | Current user's complete review/job state                                                        |
| `GET /api/v1/runtime` | 200 | Explicit environment, active model identity and readiness observation |
| `GET /health/live`                         | 200/503 | Process/coordinator liveness                                                                    |
| `GET /health/ready`                        | 200/503 | Coordinator/model readiness plus a live writable SQLite check; DynamoDB is validated at startup |

Registration/login body: `{"login_id":"reviewer","password":"a-long-example-password"}`. Identifiers are trimmed and lowercased, accept ASCII letters, digits, `.`, `_`, `@`, `+`, `-`, and must be 3–100 characters long. Passwords must be 12–128 characters long and are not normalized.

Successful authentication sets the session cookie and returns `login_id`, `csrf_token`, `expires_at` (Unix seconds), and `source_max_chars`. The browser keeps the CSRF token in memory and restores it through `/auth/me`; it never stores the session token in local storage.

Every state-changing request must have an exact matching `Origin` and JSON content type. Authenticated writes additionally require `X-CSRF-Token` matching the session. Authentication writes are protected against login CSRF by exact-origin checking and JSON-only requests; cross-origin CORS is not enabled. Native clients must send the expected origin explicitly.

For logout, send an empty JSON object (`{}`) with `Content-Type: application/json`, the normal Cookie, exact Origin and `X-CSRF-Token`; an empty request without the JSON content type returns 415.

Submission body:

```json
{
  "source_code": "def average(xs): return sum(xs) / len(xs)",
  "language": "python",
  "client_request_id": "ca4b090d-1285-4995-9b24-ae1427142920"
}
```

`language` defaults to `auto`; any valid short language hint is accepted. `client_request_id` is optional; the server generates a UUID if omitted. Browsers generate and retain it for uncertain-delivery retries. The response contains `review_id`, `status`, and `client_request_id`. An idempotent response may already be completed or failed.

The default source limit is 12,000 characters. The largest of the three complete section prompts, including instructions, must fit 2,048 tokens; the character and token limits are independently enforced. Oversized HTTP bodies are rejected at 128 KiB before JSON parsing. Nothing is compiled, parsed as an executable language, or saved as a source file.

Response models in `backend/app/api/schemas.py` explicitly allowlist fields. Detail
responses contain `review_id`, `status`, `client_request_id`, `language`, `source_code`,
`review_result`, `error_code`, `error_message`, `model_id`, `model_revision`,
`retry_count`, `created_at`, and `updated_at`. Internal user IDs and operation metadata
are never serialized. History returns `items` and nullable `next_cursor`; each item
contains only `review_id`, `language`, `status`, `error_code`, `created_at`, `updated_at`.
IDs stay strings, timestamps stay numeric Unix seconds, and absent result/error/model
values stay null. No response model excludes null values.

The optional local `ENABLE_API_DOCS=true` switch exposes `/docs` and `/openapi.json`
on the backend. Both are off by default and remain off in the maintained deployment.
It does not change session, Origin or CSRF requirements.

Errors use:

```json
{
  "error": {
    "code": "queue_full",
    "message": "The review queue is full. Try again later.",
    "request_id": "UUID"
  }
}
```

| Status  | Examples                                                                  |
| ------- | ------------------------------------------------------------------------- |
| 400     | Well-formed but unknown history cursor                                    |
| 401     | Missing/expired/revoked session; disabled account; invalid credentials    |
| 403     | Untrusted origin or mismatched CSRF token                                 |
| 404     | Missing review or review owned by another account                         |
| 409     | Existing login identifier; conflicting idempotency payload; active review |
| 413/415 | Body too large / non-JSON write                                           |
| 422     | Blank/oversized source, token limit, invalid UUID or credentials format   |
| 429     | Login/submission rate limit or full queue; includes Retry-After           |
| 503     | Model loading/draining, storage failure, unavailable account service      |

Inference errors mark the stored job as `failed`, with codes such as `inference_timeout`, `empty_model_response`, `invalid_model_response`, `inference_failed`, or `interrupted`. `invalid_model_response` means the local model returned text that did not satisfy the minimum structured, source-linked review contract. A successful GET still returns 200 when describing a failed job.

## Read-only instance runtime

`GET /api/v1/runtime` is a public, non-sensitive display endpoint (no session required).
It returns HTTP 200 when the observation succeeds, including when the service is not ready.
The existing `/health/live` and `/health/ready` HTTP status and body contracts are unchanged.

- `deployment_environment`: `minikube`, `development`, or `unknown`, from explicit
  `DEPLOYMENT_ENVIRONMENT` configuration. Default is `unknown`; neither hostname nor
  the existing logging/test `ENVIRONMENT` setting implies a deployment environment.
- `inference_mode`: `real` for the built-in Transformers adapter, `simulated` for an
  explicitly marked fixture, otherwise `unknown`. Deployment environment does not select inference.
- `model_id`, `model_revision`, `model_source`, `device`: configured pinned identity and
  `cpu` for the built-in real adapter; `Simulated model`, `fixture-v1`, `test_fixture`
  and null device for the deterministic adapter. Unrecognized adapters expose null identity,
  `unknown` source and null device. No model weights are loaded by this endpoint.
- `service_status`, `accepting_submissions`: reuse the coordinator and SQLite readiness
  check. Shutdown is reported as `shutting_down`. Readiness permits an attempt; authentication,
  CSRF, rate, input, active-review and atomic queue-capacity checks still decide admission.

This is not a cluster-health API and contains no credentials, paths, account data, pod
inventory or host metrics. It does not query Kubernetes or inspect host configuration.
The UI polls sequentially five seconds after each response, with an eight-second timeout
and unmount cancellation. Failed/malformed observations disable new submissions without
clearing input. Unknown fields/states never imply readiness; recovery restores controls.
Service status and persisted task status are shown separately.

Unconfirmed POST delivery is retried only through the explicit retry button, using the
same frozen source, language and request ID. Readiness rejections are not automatically
retried. Accepted tasks continue to be fetched from their existing history record.

New simulated result records use the fixture identity rather than the configured Qwen
identity. Existing history is not migrated: older simulated records can contain Qwen
configuration metadata, so stored metadata alone is not proof of real inference.
