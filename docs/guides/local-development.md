# Local development

Use this guide to run the frontend and backend directly for development and testing.
For a cluster deployment, follow the [Helm release guide](helm-release.md) or
[GitOps guide](gitops.md). The [Minikube development CLI](minikube-demo.md) is an
optional way to build and deploy local source on a Docker-driver cluster.

[Documentation index](../README.md) · Run commands from the repository root unless shown otherwise. Check each section's side effects before running it.

## When to use and prerequisites

Use this guide for a loopback development environment. Start with the [fake-model quick start](#test-harness) for UI/API work; use the real-model path only when inference is needed.

Use Python 3.12 and Node 24. Docker is needed only for persistent DynamoDB Local.
Ollama manages model weights and tokenization, so no additional Python model packages
are needed for real inference. Install the Python dependencies in the next section
and the frontend dependencies with:

```bash
npm --prefix frontend ci
```

The frontend uses a same-origin Vite proxy for `/api` and `/health`; do not call port 8000 directly from browser code. Open `http://localhost:5173` rather than `127.0.0.1:5173`, because origin matching is exact.

## Python 3.12 virtual environment

`backend/.venv` is a standard Python virtual environment, so it does not appear in `conda env list`. A shell can show both `(.venv)` and `(base)` when both environments are active. Check the interpreter with `which python`, `python --version`, and `python -c 'import sys; print(sys.executable)'`.

The backend requires Python `>=3.12,<3.13`. On macOS, install a persistent Python 3.12 with Homebrew and create the virtual environment from the repository root:

```bash
brew install python@3.12
```

```bash
REVIEW_PYTHON_BASE="$(brew --prefix python@3.12)/bin/python3.12"
```

```bash
"${REVIEW_PYTHON_BASE}" --version
```

```bash
"${REVIEW_PYTHON_BASE}" -m venv backend/.venv
```

```bash
backend/.venv/bin/python -m pip install -r backend/requirements-dev.lock
```

```bash
backend/.venv/bin/python -m pip install --no-deps -e backend
```

On another platform, replace `REVIEW_PYTHON_BASE` with the absolute path of a persistent Python 3.12 executable. Do not create this project environment from an interpreter under `/private/tmp`, `/tmp`, an IDE cache, or another location that may be cleaned on restart. Also do not substitute Python 3.13 or newer: the project metadata intentionally rejects it.

In IDEA, open the project Python Interpreter settings, add a local existing interpreter, and select `<repository>/backend/.venv/bin/python`. Keeping this path stable allows the environment to be rebuilt without changing the project interpreter setting. Open a new terminal or activate it explicitly after creation:

```bash
source backend/.venv/bin/activate
```

```bash
which python
```

```bash
python --version
```

```bash
python -c 'import sys; print(sys.executable)'
```

The reported executable must be under the repository's `backend/.venv`, and the version must be Python 3.12.x.

### Recover a virtual environment created from a temporary interpreter

The error `init_fs_encoding: failed to get the Python codec of the filesystem encoding`
can indicate that the base interpreter was removed or partially cleaned up. Also check
whether `backend/.venv/bin/python` points into `/private/tmp`. Rebuild the environment
with a persistent interpreter; editing `pyvenv.cfg` or individual symlinks does not make
a virtual environment portable.

From the repository root, deactivate the broken environment and keep an ignored local
backup. If `.local/backend-venv-broken` already exists, choose a different backup name
before running the move:

```bash
deactivate 2>/dev/null || true
```

```bash
mkdir -p .local
```

```bash
mv backend/.venv .local/backend-venv-broken
```

Repeat the [Python environment setup](#python-312-virtual-environment) to recreate
`backend/.venv` and install its dependencies. Ollama's model store and application
data outside the virtual environment are not affected.

## Persistent local accounts and real inference

These steps start DynamoDB Local, create its accounts table if needed, and copy the
example configuration only if `.env` does not exist. They preserve existing data and
configuration and do not create AWS resources. Run them from the repository root:

```bash
docker compose up -d dynamodb
```

```bash
DYNAMODB_ENDPOINT_URL=http://127.0.0.1:8001 backend/.venv/bin/python scripts/init_local_users.py
```

```bash
test -f backend/.env || cp backend/.env.example backend/.env
```

Replace `SIGNING_SECRET` in the ignored `.env` with a random value of at least
32 characters. Generate it locally, copy it into `.env`, and do not commit it:

```bash
backend/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

The loopback-only `COOKIE_SECURE=false` setting permits HTTP development; Origin,
HttpOnly, SameSite and CSRF protections remain enabled.

Start Ollama with `qwen3:1.7b` installed. Then start the backend from its own
directory so the settings file and relative data paths resolve correctly:

```bash
cd backend
```

```bash
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --no-proxy-headers
```

The runtime uses dummy credentials for DynamoDB Local and disables host SDK profiles,
metadata, shared configuration and proxy fallback. It rejects cloud endpoints.
Run `npm --prefix frontend run dev` in a separate terminal from the project root, then
check readiness with `curl -i http://127.0.0.1:8000/health/ready`.

DynamoDB Local persists to a Docker volume. The local Compose service intentionally runs as root only to initialize/write that named development volume; minikube application containers are non-root. `docker compose down` keeps local accounts. `docker compose down -v` deletes them and is destructive.

## Test harness

For UI/API development without model weights or Docker, run these in separate terminals
from the repository root after installing the development dependencies:

```bash
backend/.venv/bin/python scripts/local_demo.py --fake-model
```

```bash
npm --prefix frontend run dev
```

Open `http://localhost:5173`. The harness uses Moto to emulate DynamoDB and creates
no AWS resources. Accounts reset on restart, while review history stays on disk.
Registering the same login again creates a different account and does not restore
access to that history. Use a separate `--data-dir` to keep test runs apart; use
DynamoDB Local above when accounts need to persist.

The fake model returns deterministic output for UI/API checks. It does not establish
real-model quality or Minikube acceptance.

For real inference with emulated accounts, start local Ollama with the selected model
installed, then run `backend/.venv/bin/python scripts/local_demo.py` from the repository
root. Docker is not needed. The harness defaults to `http://localhost:11434` and accepts
`--ollama-base-url`. For backend and Helm model settings, see the
[model reference](../reference/model.md).

## Success criteria and tests

### Backend code map

The Uvicorn entry point is `app.main:create_app`. Its factory gives each application
instance its own settings and stores. See the
[implementation map](../reference/architecture.md#implementation-map) for module
responsibilities and the [testing guide](../testing/README.md) for test coverage.

### Canonical imports and compatibility

Use `app.api.*`, `app.inference.*` and `app.persistence.*` for their respective
implementations. `app.inference.model` defines the model protocol, and
`app.inference.ollama` supplies the real adapter.

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

For a broken interpreter, use the recovery section above. For a 403 response, open
`http://localhost:5173` exactly as written. If a port is occupied, stop only a process
you own. For model-loading failures, follow
[troubleshooting](../operations/recovery-and-cleanup.md) to inspect Ollama availability,
the selected model and safe startup events. Preserve partial downloads and existing data.

## Dependency maintenance

`backend/requirements.lock`, `requirements-dev.lock`, `requirements-model.lock`, and `frontend/package-lock.json` pin resolved dependencies. The backend Dockerfile installs only the application and Ollama HTTP client dependencies; it contains no Torch or Transformers. To update Python locks deliberately, use `uv pip compile` with Python 3.12; review and rerun all relevant checks. Review Docker base image tags before updating the local deployment.

## Instance labels in the workbench

Fixed interface copy remains English, matching the project's language convention.
The workbench distinguishes deployment environment, inference adapter, readiness and
individual review state. The minikube overlay sets `DEPLOYMENT_ENVIRONMENT=minikube`;
the direct-start `.env.example` and the local harness use `development`. Existing local
`.env` files are not rewritten: add `DEPLOYMENT_ENVIRONMENT=development` deliberately
when using that startup method. Unconfigured deployments display “Environment unknown”.
This setting changes only display metadata, not model selection or runtime policy.

The fake harness labels its inference and completed reviews as simulated. Neither mode
displays a time estimate. “About this instance” shows model, processing and storage
details; service problems stay visible beside the status strip. Local accounts isolate
review histories.
The runtime endpoint's exact semantics and backward-compatibility limits are documented
in the [API reference](../reference/api.md#instance-runtime).
