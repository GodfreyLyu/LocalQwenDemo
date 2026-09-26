# Local development

Use this guide to develop and test locally. For deployment to an existing cluster,
follow the [minikube guide](minikube-demo.md).

[Documentation index](../README.md) · Run commands from the repository root unless shown otherwise. Check each section's side effects before running it.

## When to use and prerequisites

Use this guide for a loopback development environment. Start with the [fake-model quick start](#test-harness) for UI/API work; use the real-model path only when inference is needed.

Use Python 3.12 and Node 24; Docker is needed only for persistent DynamoDB Local. Install dependencies from the lockfiles as shown in the root README. Routine API tests do not need the optional model dependencies.

The frontend uses a same-origin Vite proxy for `/api` and `/health`; do not call port 8000 directly from browser code. Open `http://localhost:5173` rather than `127.0.0.1:5173`, because origin matching is exact.

## Python 3.12 virtual environment

`backend/.venv` is a standard Python virtual environment, so it does not appear in `conda env list`. A shell can show both `(.venv)` and `(base)` when both environments are active. Check the interpreter with `which python`, `python --version`, and `python -c 'import sys; print(sys.executable)'`.

The backend requires Python `>=3.12,<3.13`. On macOS, install a persistent Python 3.12 with Homebrew and create the virtual environment from the repository root:

```bash
brew install python@3.12
REVIEW_PYTHON_BASE="$(brew --prefix python@3.12)/bin/python3.12"
"${REVIEW_PYTHON_BASE}" --version
"${REVIEW_PYTHON_BASE}" -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.lock
backend/.venv/bin/python -m pip install --no-deps -e backend
```

On another platform, replace `REVIEW_PYTHON_BASE` with the absolute path of a persistent Python 3.12 executable. Do not create this project environment from an interpreter under `/private/tmp`, `/tmp`, an IDE cache, or another location that may be cleaned on restart. Also do not substitute Python 3.13 or newer: the project metadata intentionally rejects it.

In IDEA, open the project Python Interpreter settings, add a local existing interpreter, and select `<repository>/backend/.venv/bin/python`. Keeping this path stable allows the environment to be rebuilt without changing the project interpreter setting. Open a new terminal or activate it explicitly after creation:

```bash
source backend/.venv/bin/activate
which python
python --version
python -c 'import sys; print(sys.executable)'
```

The reported executable must be under the repository's `backend/.venv`, and the version must be Python 3.12.x.

### Recover a virtual environment created from a temporary interpreter

Errors such as `init_fs_encoding: failed to get the Python codec of the filesystem encoding`, or a `backend/.venv/bin/python` symlink that resolves under `/private/tmp`, mean that the base interpreter has been removed or partially cleaned. A virtual environment is not portable across base interpreters; rebuild it rather than editing `pyvenv.cfg` or replacing individual symlinks.

From the repository root, deactivate the broken environment, retain an ignored local backup, and recreate the same `backend/.venv` path:

```bash
deactivate 2>/dev/null || true
mkdir -p .local
mv backend/.venv .local/backend-venv-broken

brew install python@3.12
REVIEW_PYTHON_BASE="$(brew --prefix python@3.12)/bin/python3.12"
"${REVIEW_PYTHON_BASE}" -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.lock
backend/.venv/bin/python -m pip install --no-deps -e backend
```

If `.local/backend-venv-broken` already exists, choose a different backup name. Real-model weights stored under `.local/models/huggingface` are separate from the virtual environment and are not removed by this procedure. Reinstall the optional model dependencies only when real inference is needed.

## Persistent local accounts and real inference

These steps start a persistent local Docker service, create its accounts table, write local configuration and install model packages. They do not create AWS resources. Existing data and `.env` must be retained. From the project root:

```bash
docker compose up -d dynamodb
DYNAMODB_ENDPOINT_URL=http://127.0.0.1:8001 backend/.venv/bin/python scripts/init_local_users.py
test -f backend/.env || cp backend/.env.example backend/.env
backend/.venv/bin/python -m pip install -r backend/requirements-model.lock 'torch==2.8.0'
```

Replace `SIGNING_SECRET` in the ignored `.env` with a random value of at least 32 characters. Generate it locally with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Do not commit it. The loopback-only `COOKIE_SECURE=false` setting permits HTTP development; normal Origin, HttpOnly, SameSite and CSRF protections remain enabled.

Start the backend from its own directory so the settings file and relative data/cache paths resolve correctly:

```bash
cd backend
AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local .venv/bin/uvicorn app.main:create_app \
  --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --no-proxy-headers
```

These literal dummy credentials are only for loopback DynamoDB Local. The runtime accepts no regional/cloud endpoint and disables host SDK profiles, metadata, shared configuration and proxy fallback. Run `npm --prefix frontend run dev` in a separate terminal from the project root. Check readiness with `curl -i http://127.0.0.1:8000/health/ready`; first startup can take many minutes while weights download.

DynamoDB Local persists to a Docker volume. The local Compose service intentionally runs as root only to initialize/write that named development volume; minikube application containers are non-root. `docker compose down` keeps local accounts. `docker compose down -v` deletes them and is destructive.

## Test harness

For UI/API development without model weights or Docker, run these in separate terminals
from the repository root after installing the development dependencies:

```bash
backend/.venv/bin/python scripts/local_demo.py --fake-model
```

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open `http://localhost:5173`. This deterministic fixture does not establish real-model
quality or minikube acceptance. Use a separate `--data-dir` when existing harness data
must be preserved.

For real inference with emulated accounts (no Docker), install the optional model dependencies shown above, then run `backend/.venv/bin/python scripts/local_demo.py` from the repository root. The first run may download pinned weights; check the resource budget and model prerequisites first.

`scripts/local_demo.py --fake-model` uses Moto's DynamoDB API emulation and a deterministic model fixture, bound only to loopback. It creates no AWS resources. Accounts reset on harness restart, so a newly registered user with the same login may not be able to access old history. For persistent accounts use DynamoDB Local above. Omit `--fake-model` to test the actual model with emulated accounts.

## Success criteria and tests

### Backend code map

The Uvicorn entry point remains `app.main:create_app`. The factory wires application
state, middleware, error handlers and routers; each router reads the current app's
state from the request, so separate app instances do not share stores or settings.

| Module under `backend/app` | Responsibility |
| --- | --- |
| `main.py` | Application construction and coordinator lifecycle |
| `api/routes/auth.py`, `api/routes/reviews.py`, `api/routes/health.py` | Account, review and health endpoints |
| `api/schemas.py`, `api/auth.py` | Request validation and session/CSRF checks |
| `rate_limit.py` | Shared per-application admission quotas, independent of HTTP/authentication |
| `health.py`, `api/routes/runtime.py`, `inference/identity.py` | Readiness observation, runtime HTTP presentation and typed adapter identity |
| `api/middleware.py`, `api/http_errors.py` | Bounded request bodies, response headers and safe error responses |
| `logging.py` | Structured logging and field allowlists |
| `coordinator.py` | Serial queue processing, persistence and timeout draining |
| `review_service.py`, `api/dependencies.py`, `domain.py` | Admission policy, typed HTTP dependencies and internal record/result types |
| `inference/model.py`, `inference/generation.py` | Model loading, tokenization, serial inference, sampling policy and metrics |
| `inference/prompts.py`, `inference/review_output.py` | Pure prompt construction and output normalization/validation |
| `persistence/storage.py`, `persistence/users.py` | SQLite history/sessions and local DynamoDB accounts |

Transport regression tests also cover chunked body limits and disconnects. API tests
cover separate application instances, and model tests cover failures before generation
as well as partial-generation diagnostics.

### Canonical imports and compatibility

Use `app.api.*`, `app.inference.*` and `app.persistence.*` for moved implementations.
The startup command remains `app.main:create_app`; package discovery already includes
`app*`. `app.model` explicitly re-exports its established model/helper API and logger
for older scripts, while `app.auth` retains only `RateLimiter`. These facades do not
contain implementations and are not internal dependencies. Monkeypatch the canonical
implementation module, for example `app.inference.model` or
`app.persistence.users.local_client`, rather than the compatibility facade.

### Optional interface documentation

For a local interview/demo session, set `ENABLE_API_DOCS=true` explicitly when
starting the backend. Swagger UI is then available at `http://127.0.0.1:8000/docs`
and its schema at `/openapi.json` on that backend. Both default to disabled; ReDoc
stays disabled. No deployment manifest enables this switch, and the existing
frontend proxy does not publish these paths. The schema documents success field
allowlists and the custom error envelope, including 422 validation errors.

Interactive writes still require the configured Origin, session cookie and CSRF
header. The UI is for inspecting contracts; it does not bypass authentication or
provide a special login path. Use the regular frontend or test harness for the
complete user flow. See [architecture](../reference/architecture.md) for the request
walkthrough, type choices, state transitions and multi-process limitations.

### Verification

With the fake harness, the API must report ready, registration/login must work in the browser, and results must be labeled as deterministic output. Use the [testing guide](../testing/README.md) for quick, complete, browser and opt-in real-model checks.

## Common failures and handling

For a broken interpreter, use the recovery section above. For a 403, open `http://localhost:5173` exactly; for occupied ports, stop only the process you own. For model-loading failures, inspect the pinned cache and safe startup events using [troubleshooting](../operations/recovery-and-cleanup.md); preserve partial downloads and data.

## Dependency maintenance

`backend/requirements.lock`, `requirements-dev.lock`, `requirements-model.lock`, and `frontend/package-lock.json` pin resolved dependencies. The backend Dockerfile installs a pinned CPU-only PyTorch wheel to avoid CUDA packages on the demo node. To update Python locks deliberately, use `uv pip compile` with Python 3.12; review and rerun all relevant checks. Review Docker base image tags before updating the local deployment.

## Instance labels in the workbench

Fixed interface copy remains English, matching the project's language convention.
The workbench distinguishes deployment environment, inference adapter, readiness and
individual review state. The minikube overlay sets `DEPLOYMENT_ENVIRONMENT=minikube`;
the direct-start `.env.example` and the local harness use `development`. Existing local
`.env` files are not rewritten: add `DEPLOYMENT_ENVIRONMENT=development` deliberately
when using that startup method. Unconfigured deployments display “Environment unknown”.
This setting changes only display metadata, not model selection or runtime policy.

The fake harness visibly reports simulated inference. Its newly completed records have
fixture provenance. Neither real nor simulated mode displays a time estimate. The centered
account form and compact workbench status strip share the same light styling. “About this
instance” holds model provenance, processing and storage details; actionable service
problems stay visible beside the status strip. Local accounts isolate review histories.
The runtime endpoint's exact semantics and backward-compatibility limits are documented
in the [API reference](../reference/api.md#read-only-instance-runtime).
