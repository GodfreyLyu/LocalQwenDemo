# Script entry points and usage

Run all commands below from the **repository root**. Follow the
[local development guide](../docs/guides/local-development.md) to set up a Python 3.12
environment at `backend/.venv`. The full check suite also requires frontend
dependencies, Git, kubectl, and Helm. Use `backend/.venv/bin/python` for Python
scripts and `bash` for shell scripts. The check scripts and Minikube shell entry
point accept `REVIEW_PYTHON` to override the interpreter path.
The backend development dependencies supply Ruff, pytest, HTTPX, PyYAML, boto3 and
the local service doubles. Check commands expect these tools to be installed.

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
| `validate_monitoring.py` | **Automatic invocation / Manual entry point** | Run `backend/.venv/bin/python scripts/validate_monitoring.py --promtool /path/to/promtool` for rendered rules, alert scenarios and dashboard queries. Accepts `--root` for candidate data; never contacts a cluster. |
| `check_monitoring.sh` | **Automatic invocation** | CI wrapper downloads checksum-verified Prometheus 3.15.0 for Linux amd64 and calls `validate_monitoring.py`. Requires `RUNNER_TEMP`, `gh`, Helm and Python with PyYAML; outside the offline gate. |
| `local_demo.py` | **Automatic invocation / Manual entry point** | Playwright starts it with `--fake-model` and an isolated data directory. For manual development, run `backend/.venv/bin/python scripts/local_demo.py --fake-model`. Omitting `--fake-model` uses real Ollama inference. |
| `init_local_users.py` | **Manual entry point** | Initializes DynamoDB Local from the host. Requires `DYNAMODB_ENDPOINT_URL`; see the example below. **Does not support `--help`.** |
| `minikube_demo.sh` | **Manual entry point** | Development CLI for Docker-driver Minikube profiles: `bash scripts/minikube_demo.sh COMMAND --profile NAME`. Use standard Helm for krunkit clusters. |
| `minikube_helm.py` | **Automatic invocation / Manual entry point** | Receives arguments from `minikube_demo.sh`. The equivalent direct command is `backend/.venv/bin/python scripts/minikube_helm.py COMMAND --profile NAME`. |
| `evaluate_model.py` | **Manual entry point** | Run `backend/.venv/bin/python scripts/evaluate_model.py --dry-run` to create a plan. Real inference requires an explicit `--run-real-model` flag. |
| `release/publish.py` | **Automatic invocation** | The release workflow calls `prepare`, `publish`, and `supersede`, or `prepare-rollback`, `check-images` and `publish`; deployment validation calls `verify`; post-merge tagging calls `finalize`. These steps depend on workflow context. Use the workflows to start releases, rollbacks or tagging. |
| `release/snapshot.py` | **Automatic invocation** | The release workflow calls `render --plan … --output … --backend-digest … --frontend-digest … --ollama-digest …` to generate a deployment snapshot. This script does not deploy to a cluster. |

## Common manual commands

Choose the offline check that matches the scope you need. The full suite already
includes the script checks:

```bash
bash scripts/check.sh
```

```bash
bash scripts/check_scripts.sh
```

```bash
bash scripts/check_minikube_demo.sh
```

```bash
backend/.venv/bin/python scripts/validate_helm.py
```

These checks exclude `real_model` and `requires_cluster` tests. They can write caches,
temporary test data and `frontend/dist`, and some tests start temporary loopback
servers. They do not install dependencies, build container images or change a live
cluster or cloud service. Failed checks return nonzero; aggregate suites stop at the
first failure, including missing tools.

For UI/API development without a real model, run the following in separate terminals:

In the first terminal, start the API with a fake model. Accounts reset on restart;
the signing key also changes, invalidating sessions. Review history stays in
`.local/demo`:

```bash
backend/.venv/bin/python scripts/local_demo.py --fake-model
```

The harness binds to loopback. `--port` defaults to 8000 and `--data-dir` to
`.local/demo`. Real inference uses `--ollama-base-url`, which defaults to
`http://localhost:11434`; Ollama handles tokenization.

In the second terminal, install frontend dependencies and start the development server:

```bash
npm --prefix frontend ci
```

```bash
npm --prefix frontend run dev
```

Open `http://localhost:5173`. To use persistent local accounts, start DynamoDB Local
with Docker Compose and run the dedicated initializer:

```bash
docker compose up -d dynamodb
```

```bash
DYNAMODB_ENDPOINT_URL=http://127.0.0.1:8001 backend/.venv/bin/python scripts/init_local_users.py
```

The initializer uses boto3 and fixed local dummy credentials to create
`llm-review-users` if needed, preserving existing accounts. This writes to the local
database. It accepts only loopback addresses with an explicit port and has no CLI
options, including `--help`. For backend startup
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
```

```bash
bash scripts/minikube_demo.sh doctor --profile minikube
```

```bash
bash scripts/minikube_demo.sh up --profile minikube
```

```bash
bash scripts/minikube_demo.sh verify --profile minikube
```

```bash
bash scripts/minikube_demo.sh port-forward --profile minikube
```

`init` creates the namespace and signing Secret. `up` builds and loads images, then
deploys the application. `verify` checks real inference and persistence. This entry
point does not start a cluster. For krunkit GPU clusters, use
[standard Helm deployment](../docs/guides/helm-release.md); for instances managed by
Argo CD, follow the [GitOps workflow](../docs/guides/gitops.md).

Every operational command requires `--profile NAME`. Keep `--namespace` and
`--release` consistent across commands; `-f` supplies values and `--state-root`
selects private local records. The CLI also provides `status`, `logs`, `rollback`
and `undeploy`. Undeploy retains data by default; purging it requires explicit
confirmation. Use `--help` for all supported options.

For model evaluation, choose either a plan or a real run. A real run also requires a
running Ollama service with the selected model installed. No extra Python model
packages are needed:

```bash
backend/.venv/bin/python scripts/evaluate_model.py --dry-run
```

```bash
backend/.venv/bin/python scripts/evaluate_model.py --run-real-model --case average
```

Reports are written to `.local/model-evaluations/`. Only the fixed synthetic fixtures
are accepted. The evaluator does not install dependencies, start services or access
AWS/Kubernetes. See the [evaluation guide](../docs/testing/model-evaluation.md) for
settings isolation, [exit codes and human review](../docs/testing/model-evaluation.md#acceptance-contract),
and [report contents and worker supervision](../docs/testing/model-evaluation.md#reports-and-supervision).

## Automatic invocation and release commands

`check.sh` calls `checks/all.sh`, which runs backend lint/tests, frontend
lint/tests/build, then `checks/scripts.sh`. The standalone `check_scripts.sh` calls
that same script gate. It syntax-checks maintained shell scripts, lints script code
and tests once, runs all script test domains, and validates both Helm Charts.
`check_minikube_demo.sh` calls `checks/deployment.sh` for the deployment subset;
`check_helm_schema.sh` calls `checks/schema.sh` for Linux CI schema validation.
Browser tests are separate locally and run in CI with the fake-model harness.

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
- [Tag approved release](../.github/workflows/release-finalize.yml): `finalize`
  verifies the merged deployment PR and creates a deployment/source tag pair.

**Prepare release** also accepts `rollback_tag` and `rollback_reason`. It restores
the approved snapshot without rebuilding images and opens a new candidate under
the same version sequence. Publication is paused manually at deployment PR merge
time; CI and Argo sync continue. See the [recovery runbook](../docs/guides/release-rollback.md)
for tag protection setup, rollback, source repair and recovery of tagging failures.

Merging into `main` triggers the release workflow automatically. To trigger it
manually, authenticate `gh` with the required repository permissions, then choose
one of the following commands from the repository root. When release inputs change,
the workflow publishes the affected images and proposes a deployment candidate PR.
An unchanged release reuses the existing result.
Release image builds and publication are separate from the offline check suite;
CI has no cluster access.

Build images as needed based on changes:

```bash
gh workflow run release-candidate.yml --ref main
```

Or force all three images to rebuild:

```bash
gh workflow run release-candidate.yml --ref main -f rebuild=true
```

Use these help commands to inspect the underlying release options. The workflow
supplies the token, run metadata, image digests, and working directories needed for
an actual release:

```bash
backend/.venv/bin/python scripts/release/publish.py --help
```

```bash
backend/.venv/bin/python scripts/release/snapshot.py --help
```

## Organization and dependency boundaries

The public entry points call the modules below. Edit these modules when developing
the tooling, and use the stable entry points above when running it. Executing
implementation files directly may leave import paths or runtime context incomplete.
Root Python entries and `release/{publish,snapshot}.py` delegate to packages. New
code should import those packages rather than wrappers. The public entries and
pytest configure `scripts/` as the internal import root.

| Path | Usage | Entry point or responsibility |
| --- | --- | --- |
| `checks/*.sh` | **Internal implementation** | Check logic called by `check*.sh`; `common.sh` is sourced only by other check scripts. |
| `dev/*.py` | **Internal implementation** | Implementations of `local_demo.py` and `init_local_users.py`. |
| `deployment/helm/*.py` | **Internal implementation** | CLI, sessions, builds, diagnostics, lifecycle operations, and acceptance checks for `minikube_helm.py`. |
| `deployment/common/*.py` | **Internal implementation** | Shared deployment target, port forwarding, queue protection, and resource logic. |
| `validation/*.py` | **Internal implementation** | Helm, snapshot and GitOps validation used by `validate_helm.py`, plus monitoring validation used by `validate_monitoring.py`. |
| `release/_entry.py`, `release/publisher.py`, `release/snapshot_impl.py`, `release/github.py`, `release/lifecycle.py`, `release/metadata.py` | **Internal implementation** | Import path setup, publishing, snapshot generation, GitHub requests, approved release tags, rollback preparation and shared provenance validation. |
| `evaluation/` | **Internal implementation** | Configuration, fixtures, execution, metrics, and reports for `evaluate_model.py`. |
| `tooling_paths.py` and package `__init__.py` files | **Internal implementation** | Path setup and package structure. |
| `tests/` | **Internal implementation** | Regression tests collected by the check scripts or pytest. To run a single domain, use a command such as `backend/.venv/bin/python -m pytest scripts/tests/tooling`. |

`minikube_demo.sh` resolves the checkout from its own path and invokes
`minikube_helm.py`, which calls `deployment.helm.cli.entry()` and `main()`.
Target discovery lives in `deployment/common/target.py`; `Session` handles private
records and locking. Helm's `build.py`, `diagnostics.py`, `lifecycle.py` and `verify.py`
handle builds, resource checks, removal and acceptance respectively.

`deployment/common` does not import the Helm CLI or session. Its helpers receive
command builders, environments or executors from callers; target ownership and
release-specific deletion rules stay in the Helm implementation. Helm and GitOps
validators share `validation/resources.py`, with snapshot orchestration in
`validation/snapshot.py`; neither validator imports the other's CLI. Release commands
share GitHub request encoding in `release/github.py` while retaining their own
transport and failure policies. Evaluator reports fingerprint the implementation
modules, public entry and shared path bootstrap.

See the [testing guide](../docs/testing/README.md) for test coverage and
[fixture maintenance](../docs/testing/README.md#mock-boundaries-and-fixture-maintenance).
