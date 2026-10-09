# Script entry points and usage

Run all commands below from the **repository root**. Follow the
[local development guide](../docs/guides/local-development.md) to set up a Python 3.12
environment at `backend/.venv`. The full check suite also requires frontend
dependencies, Git, kubectl, and Helm. Use `backend/.venv/bin/python` for Python
scripts and `bash` for shell scripts. The check scripts and Minikube shell entry
point accept `REVIEW_PYTHON` to override the interpreter path.

- **Automatic invocation**: run by CI, test tools, or another entry point. The table
  identifies the caller; you do not need to run these steps individually.
- **Manual entry point**: a stable command for developers to run directly. Some
  commands are also invoked automatically.
- **Internal implementation**: a module imported or called by an entry point,
  rather than a standalone operational command.

## Entry point reference

Paths in the first column are relative to `scripts/`. Commands with both automatic
and manual uses carry both labels.

| File | Usage | Caller or command |
| --- | --- | --- |
| `check.sh` | **Manual entry point** | Run `bash scripts/check.sh` for the full offline suite: backend, frontend, and script checks. Browser tests, CI schema validation, and real-model checks have separate entry points. |
| `check_scripts.sh` | **Automatic invocation / Manual entry point** | Called directly by CI. Run `bash scripts/check_scripts.sh` locally to check all script test domains and Helm configuration. The full suite also uses its internal implementation. |
| `check_minikube_demo.sh` | **Manual entry point** | Run `bash scripts/check_minikube_demo.sh` for deployment script checks and offline regression tests. It does not deploy to a cluster. |
| `check_helm_schema.sh` | **Automatic invocation** | CI runs `bash scripts/check_helm_schema.sh`. Pass a snapshot root as the first argument to validate a snapshot. Requires Linux amd64, `RUNNER_TEMP`, `gh`, Helm, and `sha256sum`; downloads a pinned version of kubeconform. It cannot run directly on macOS. |
| `validate_helm.py` | **Automatic invocation / Manual entry point** | Called by script checks and release workflows. Run `backend/.venv/bin/python scripts/validate_helm.py` locally; add `--root /path/to/snapshot --snapshot` to validate a snapshot. |
| `local_demo.py` | **Automatic invocation / Manual entry point** | Playwright starts it with `--fake-model` and an isolated data directory. For manual development, run `backend/.venv/bin/python scripts/local_demo.py --fake-model`. Omitting `--fake-model` uses real Ollama inference. |
| `init_local_users.py` | **Manual entry point** | Initializes DynamoDB Local from the host. Requires `DYNAMODB_ENDPOINT_URL`; see the example below. **Does not support `--help`.** |
| `minikube_demo.sh` | **Manual entry point** | Preferred entry point for Minikube operations: `bash scripts/minikube_demo.sh COMMAND --profile NAME`. See the deployment sequence below. |
| `minikube_helm.py` | **Automatic invocation / Manual entry point** | Receives arguments from `minikube_demo.sh`. The equivalent direct command is `backend/.venv/bin/python scripts/minikube_helm.py COMMAND --profile NAME`. |
| `evaluate_model.py` | **Manual entry point** | Run `backend/.venv/bin/python scripts/evaluate_model.py --dry-run` to create a plan. Real inference requires an explicit `--run-real-model` flag. |
| `release/publish.py` | **Automatic invocation** | The release workflow calls `prepare`, `publish`, and `supersede`; the deployment validation workflow calls `verify`. These steps depend on workflow context. Use the workflow to start a release. |
| `release/snapshot.py` | **Automatic invocation** | The release workflow calls `render --plan … --output … --backend-digest … --frontend-digest … --ollama-digest …` to generate a deployment snapshot. This script does not deploy to a cluster. |

## Common manual commands

Choose the offline check that matches the scope you need. The full suite already
includes the script checks:

```bash
bash scripts/check.sh
bash scripts/check_scripts.sh
bash scripts/check_minikube_demo.sh
backend/.venv/bin/python scripts/validate_helm.py
```

For UI/API development without a real model, run the following in separate terminals:

```bash
# Terminal 1: start the local API with a fake model; accounts reset on restart
backend/.venv/bin/python scripts/local_demo.py --fake-model
```

```bash
# Terminal 2: install frontend dependencies and start the development server
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open `http://localhost:5173`. To use persistent local accounts, start DynamoDB Local
with Docker Compose and run the dedicated initializer:

```bash
docker compose up -d dynamodb
DYNAMODB_ENDPOINT_URL=http://127.0.0.1:8001 \
  backend/.venv/bin/python scripts/init_local_users.py
```

The initializer creates the users table if needed and preserves existing accounts.
It accepts only loopback addresses with an explicit port. For backend startup
instructions with persistent accounts, see the
[local development guide](../docs/guides/local-development.md#persistent-local-accounts-and-real-inference).
Helm deployments initialize the table automatically through the Chart's
`initialize-users` init container, so they do not need this host-side script.

Minikube deployment requires an **existing, running single-node profile using the
Docker driver** and a ready Ollama Service. See the
[Minikube guide](../docs/guides/minikube-demo.md) for prerequisites. Run these commands
in order to initialize, check, deploy, verify, and access the application:

```bash
bash scripts/minikube_demo.sh init --profile minikube
bash scripts/minikube_demo.sh doctor --profile minikube
bash scripts/minikube_demo.sh up --profile minikube
bash scripts/minikube_demo.sh verify --profile minikube
bash scripts/minikube_demo.sh port-forward --profile minikube
```

`init` creates the namespace and signing Secret. `up` builds and loads images, then
deploys the application. `verify` checks real inference and persistence. This entry
point does not start a cluster. For krunkit GPU clusters, use
[standard Helm deployment](../docs/guides/helm-release.md); for instances managed by
Argo CD, follow the [GitOps workflow](../docs/guides/gitops.md).

For model evaluation, choose either a plan or a real run. A real run also requires a
running Ollama service with the selected model installed. No extra Python model
packages are needed:

```bash
backend/.venv/bin/python scripts/evaluate_model.py --dry-run
backend/.venv/bin/python scripts/evaluate_model.py --run-real-model --case average
```

Reports are written to `.local/model-evaluations/`. See the
[evaluation guide](../docs/testing/model-evaluation.md) for all options and the
human review required for full semantic acceptance.

## Automatic invocation and release commands

These files define the automated call chains:

- [Quality checks workflow](../.github/workflows/ci.yml): script checks, schema
  validation, and browser tests.
- [Playwright configuration](../frontend/playwright.config.ts): starts
  `local_demo.py --fake-model`.
- [Prepare release](../.github/workflows/release-candidate.yml): `prepare` → build
  images → `snapshot.py render` → `validate_helm.py` → `publish` → deployment
  validation → `supersede`.
- [Validate release snapshot](../.github/workflows/deployment-validation.yml):
  `publish.py verify`, snapshot validation, and schema checks.

Merging into `main` triggers the release workflow automatically. To trigger it
manually, authenticate `gh` with the required repository permissions, then choose
one of the following commands from the repository root. The workflow pushes images
and creates a deployment candidate PR:

```bash
# Build images as needed based on changes
gh workflow run release-candidate.yml --ref main

# Force all three images to rebuild
gh workflow run release-candidate.yml --ref main -f rebuild=true
```

Use these help commands to inspect the underlying release options. The workflow
supplies the token, run metadata, image digests, and working directories needed for
an actual release:

```bash
backend/.venv/bin/python scripts/release/publish.py --help
backend/.venv/bin/python scripts/release/snapshot.py --help
```

## Internal implementation

The public entry points call the modules below. Edit these modules when developing
the tooling, and use the stable entry points above when running it. Executing
implementation files directly may leave import paths or runtime context incomplete.

| Path | Usage | Entry point or responsibility |
| --- | --- | --- |
| `checks/*.sh` | **Internal implementation** | Check logic called by `check*.sh`; `common.sh` is sourced only by other check scripts. |
| `dev/*.py` | **Internal implementation** | Implementations of `local_demo.py` and `init_local_users.py`. |
| `deployment/helm/*.py` | **Internal implementation** | CLI, sessions, builds, diagnostics, lifecycle operations, and acceptance checks for `minikube_helm.py`. |
| `deployment/common/*.py` | **Internal implementation** | Shared deployment target, port forwarding, queue protection, and resource logic. |
| `validation/*.py` | **Internal implementation** | Helm, snapshot, and GitOps validation used by `validate_helm.py`. |
| `release/_entry.py`, `release/publisher.py`, `release/snapshot_impl.py`, `release/github.py` | **Internal implementation** | Import path setup, publishing, snapshot generation, and GitHub requests for `release/publish.py` and `release/snapshot.py`. |
| `evaluation/` | **Internal implementation** | Configuration, fixtures, execution, metrics, and reports for `evaluate_model.py`. |
| `tooling_paths.py` and package `__init__.py` files | **Internal implementation** | Path setup and package structure. |
| `tests/` | **Internal implementation** | Regression tests collected by the check scripts or pytest. To run a single domain, use a command such as `backend/.venv/bin/python -m pytest scripts/tests/tooling`. |

See the [script reference](../docs/reference/scripts.md) for detailed dependencies,
side effects, and call chains, and the [testing guide](../docs/testing/README.md)
for test coverage.
