# Security and privacy

Audience: maintainers. Purpose: understand trust and sensitive-data boundaries. Prerequisites: basic familiarity with the root README; no running environment required.

[Documentation index](../README.md)

## Authentication

Passwords use Argon2id with a fresh random salt, memory cost 19,456 KiB, two iterations, and one lane. Identifiers are normalized before DynamoDB's conditional write. Passwords are neither stored in plaintext nor reversibly encrypted. Unknown accounts use a dummy hash verification, and invalid-login messages do not reveal which credential failed.

Sessions have a 30-minute absolute lifetime, no refresh mechanism, and a signed random opaque token. With Secure cookies enabled, the browser cookie is `__Host-review_session`, Secure, HttpOnly, SameSite=Strict, Path=/, and has no Domain. A hashed token record in SQLite supports immediate logout revocation. Every authenticated request verifies the current DynamoDB account and disabled flag. Rotating the signing secret and restarting the backend invalidates all outstanding sessions.

Authenticated state changes require both exact Origin validation and a session-bound `X-CSRF-Token`. Register/login require the exact Origin and JSON content type; CORS is deliberately absent. The maintained loopback HTTP entry explicitly uses `COOKIE_SECURE=false` and a separate cookie name. HttpOnly, SameSite=Strict, Origin and CSRF protections remain in force; HTTPS/Secure-cookie behavior is still covered by API tests. The service is not configured for public exposure.

Login/registration have per-IP and per-normalized-identifier sliding-window limits (10/minute). Submissions are limited to 6/minute per authenticated user. Limiter memory is bounded and resets on restart. The IP key uses only the direct peer; client-supplied forwarding headers cannot grant a new rate-limit identity. This is basic single-process demo throttling, not a distributed abuse-prevention system.

## Data and model boundaries

Source and review text are private per user and stored in SQLite on a local persistent volume. Sessions use that same volume; account/password records use a separate DynamoDB Local volume. Encryption at rest is not provided or verified by this project. Database queries are parameterized, and user identity always comes from the verified session. Source files are not created. Source text is never passed to a shell, interpreter, compiler, agent, or external inference API.

Input is bounded by HTTP bytes, characters, and model prompt tokens. Output token count, queue size, retry count, and inference concurrency are bounded. The model has no tools. Prompt injection can still influence review quality; model text is always untrusted. Three independent prompts reuse only the original untrusted source, never a previously generated section. Qwen3's tokenizer receives the hard `enable_thinking=False` chat-template option; the application does not request, parse, store, or log chain-of-thought. Backend code inserts the fixed Markdown headings, but every review body remains model-generated. For a section that reaches its token limit, deterministic post-processing can only remove the unfinished suffix after the last complete terminator; it cannot add or rewrite a conclusion, and absence of any complete boundary fails closed. Sampling uses an ephemeral SHA-256-derived seed per source and section inside an isolated CPU RNG context; neither the seed nor a source hash is logged or persisted. Empty bodies, unexpected reserved headings, capped bodies without a complete terminator, and results without any recognizable source-identifier link are rejected with a fixed public error and without saving or logging the rejected content. This catches gross drift only and is not semantic verification.

React Markdown skips raw HTML, applies `rehype-sanitize`, and does not load images from generated content. Unsafe link schemes are removed. External links use `noopener noreferrer`. Nginx adds a restrictive Content Security Policy, frame blocking, no-referrer, HSTS, and content-type protection. The style policy permits inline styles for CodeMirror; script execution remains restricted to bundled same-origin scripts.

## Network and containers

The default entry binds loopback on the host through the verified minikube API. All
application resources are namespaced and privately owned; the cluster itself need not
belong to this project. The scripts never install or alter CNI/storage components.
NetworkPolicy denies traffic by default and permits only the frontend/backend/account,
DNS and public HTTPS model-download paths in the rendered manifest. Policy enforcement
depends on the existing CNI: a YAML policy is not proof of working network isolation.
Link-local metadata is excluded; standard NetworkPolicy cannot restrict download HTTPS
to Hugging Face by domain. Source text is not sent with model-download requests.

DynamoDB Local has an explicit HTTP endpoint restricted to loopback with an explicit
port, or the exact `review-dynamodb` service names on port 8000. Missing endpoints,
regional/public hosts, embedded credentials, URL queries/fragments and alternate paths
are rejected. boto3 uses a new session with literal `local` access/secret keys, disabled
metadata, empty shared configuration and no proxy/SDK endpoint override. These inert
credentials are protocol requirements, not permission to access a cloud account.

Application containers run non-root, with read-only root filesystems, no added Linux capabilities, no privilege escalation, RuntimeDefault seccomp, and bounded resources. Writable backend locations are `/data`, `/models/huggingface`, and `/tmp`. Kubernetes system add-ons may require host privileges; these are separate from the hardened application namespace.

## Logging and secrets

Structured application logs always contain UTC timestamp, level, service, environment, event, and only explicitly allowlisted optional fields. HTTP records add a fixed method, nonnegative monotonic duration, fixed error code, and only an allowlisted FastAPI route template; no unknown route falls back to the raw path. Successful health probes are suppressed, failed probes stay visible, and `X-Request-ID` remains available for correlation. Review submit/start/finish/reject events add opaque review ID and bounded queue/timing/outcome fields. A rejected model response may add only a fixed validation-reason enum; an unknown reason is omitted. Generation completion adds only summed monotonic duration, total generated-token count, the configured global limit and flag, and integer/boolean metrics under the three fixed section names, including whether an incomplete capped suffix was removed. `release_sha` is omitted unless the runtime receives a validated real SHA.

Removed text, removed character counts, punctuation, request/response bodies, raw path/URL/query, source code, source-derived hashes, generation seeds, prompts, token IDs or generated token content, decoded model output, passwords, cookies, tokens, usernames, and full exception messages are excluded. API validation errors never echo raw input. Uvicorn and frontend access logging are disabled, to prevent arbitrary path/query values from entering logs. Unexpected model/storage messages are translated to fixed public errors.

The deployment script creates a random signing Secret only when no owned signing Secret
exists. Repeated up reuses it; cleanup preserves it by default. Namespace UID, ownership
marker and per-resource checks prevent adoption of unknown Secrets. Secret values and
arbitrary annotations are not returned by the ownership metadata projection. Shared
state directories are mode 0700 and files mode 0600; private kubeconfig and acceptance
credentials must never be published. See the [log contract](observability.md).

Ignored files cover `.env`, local data/model caches, state/plans, credentials-shaped local configuration, build outputs, and editor files. No model weights or reference-project local state belong in version control. Local demo harness credentials are deliberately inert dummy strings and work only against emulated/local DynamoDB.

## Remaining trust and retention limits

Local administrators and the application can read persisted source. User history isolation
is application authorization, not per-user disk encryption. PVCs and backing directories
can retain data after workload deletion; no automatic expiry, secure erasure or backup
is provided. Review retention with participants and use the explicit cleanup procedures.
There is no claimed production SLA, audit certification, public multi-tenant hardening
or distributed rate limiting. Host and Docker security remain operator responsibilities.
