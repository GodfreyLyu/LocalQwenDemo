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
