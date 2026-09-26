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

With the fake harness, the API must report ready, registration/login must work in the browser, and results must be labeled as deterministic output. Use the [testing guide](../testing/README.md) for quick, complete, browser and opt-in real-model checks.

## Common failures and handling

For a broken interpreter, use the recovery section above. For a 403, open `http://localhost:5173` exactly; for occupied ports, stop only the process you own. For model-loading failures, inspect the pinned cache and safe startup events using [troubleshooting](../operations/recovery-and-cleanup.md); preserve partial downloads and data.

## Dependency maintenance

`backend/requirements.lock`, `requirements-dev.lock`, `requirements-model.lock`, and `frontend/package-lock.json` pin resolved dependencies. The backend Dockerfile installs a pinned CPU-only PyTorch wheel to avoid CUDA packages on the demo node. To update Python locks deliberately, use `uv pip compile` with Python 3.12; review and rerun all relevant checks. Review Docker base image tags before updating the local deployment.
