# Script catalog and call chains

Use this catalog to check command requirements and side effects. Set up the [local environment](../guides/local-development.md) and the tools listed below. Run examples from the repository root, invoking Python files through `backend/.venv/bin/python`. Shell entries are executable.

## Organization and dependency boundaries

Public file paths remain stable for workflows, Playwright and operator commands.
Root Python files and `release/{publish,snapshot,migrate,bootstrap}.py` delegate to
package implementations; new code imports the packages rather than these wrappers.
Internal Python imports use `scripts/` as their import root, configured by pytest or
the public entry. Run the public files below instead of executing implementation files.

```text
scripts/
  checks/                 # all, scripts, deployment and CI schema gates
  dev/                    # Local API harness and users initialization
  deployment/
    common/               # Target, forwarding, queue guard, acceptance, files, units, IPv4
    helm/                 # CLI, session, build, diagnostics, lifecycle and verification
    legacy/               # Explicit Kustomize compatibility/recovery and old installer
  validation/             # Resource invariants, snapshots, Helm and GitOps validation
  release/                # Snapshot generation, publishing, migration and GitHub requests
  evaluation/             # CLI, configuration, metrics, quality, reports and worker supervision
  tests/                  # Domain regressions and cross-domain tooling contracts
  tooling_paths.py        # Checkout paths and backend import preparation
```

`deployment/common` does not import either deployment implementation. Helm imports
legacy only when dispatching the explicit `legacy` command. Shared helpers receive
command builders, environments or executors from their callers; target ownership and
release-specific deletion rules remain in their owning modules. Legacy helpers obtain
an explicitly scoped operation context from `legacy/context.py`; they do not import the
CLI or discover another target. The context still holds the existing legacy runtime
state, so this is not a new concurrent multi-target deployment API.

Helm and GitOps validators share `validation/resources.py`; snapshot orchestration
lives in `validation/snapshot.py`. Neither validator imports the other's CLI.
Release commands share GitHub request encoding in `release/github.py`, while callers
retain their own transport and failure policies. Evaluator reports fingerprint all
implementation modules as well as the public entry and shared path bootstrap.

## Local commands and checks

| Entry / invocation | Inputs, options and environment | Dependencies | Output, generated files and effects |
| --- | --- | --- | --- |
| `bash scripts/check.sh` | No options; do not export `RUN_REAL_MODEL=1` for routine checks | Python dev lock, npm dependencies, Git, kubectl, Helm | Lint/format, backend tests, frontend lint/tests/build, local script regressions, manifest and Helm structural checks; caches and `frontend/dist`; fail-fast nonzero; no install/build-image/deploy |
| `scripts/check_scripts.sh` | `REVIEW_PYTHON` overrides the default interpreter; runs all script domains, manifest validation and Helm checks | Python dev lock, Bash, Git, kubectl, Helm | Shell syntax includes all maintained entry points; temporary test files/loopback children only; no real cloud/cluster writes |
| `scripts/check_minikube_demo.sh` | `REVIEW_PYTHON` overrides default `backend/.venv/bin/python` | Ruff, pytest, HTTPX, PyYAML, kubectl, Helm | Lint/format and all tests under `scripts/tests/deployment`; real offline render and loopback socket/process tests; no cluster |
| `backend/.venv/bin/python scripts/local_demo.py --fake-model` | `--fake-model` opt-in; **default uses real model**; `--port` default 8000; `--data-dir` default `.local/demo` | Dev lock; model client lock and local Ollama when real | Loopback API; Moto accounts reset and random signing key changes on restart; persistent SQLite in data dir; real mode uses `--ollama-base-url` (default localhost:11434) and uses Ollama tokenization |
| `backend/.venv/bin/python scripts/init_local_users.py` | No CLI parser/options; required explicit `DYNAMODB_ENDPOINT_URL`; only loopback with an explicit port allowed; fixed dummy credentials | boto3, running DynamoDB Local | Describes and idempotently creates `llm-review-users`; **local database write**. Do not invoke with `--help`: it is not a help-capable script |
| `backend/.venv/bin/python scripts/validate_manifests.py` | No CLI parser/options; minikube overlay fixed relative to script root | PyYAML, kubectl | Offline `kubectl kustomize`, assert resource/security/model contracts; console only. `--help` would still execute validation |

Test fixtures are covered in [testing](../testing/README.md#mock-boundaries-and-fixture-maintenance). Scripts return nonzero on failed assertions/subcommands; aggregate gates stop on first failure rather than silently skipping missing tools.

## Current-model evaluation entry

`backend/.venv/bin/python scripts/evaluate_model.py` creates only a plan by default,
without source or model output. Choose `--dry-run` or `--run-real-model`; these options
are mutually exclusive. Select cases with `--case
all|hello_world|average|square|first_item|sql_injection|prompt_injection`. Add `--review-in-terminal` to view synthetic
output privately and record human judgments. Use `--ollama-base-url`, `--ollama-model-digest`, `--max-output-tokens` and
`--timeout-seconds` to match the application configuration. No arbitrary source option exists.

Plans require the Python dev environment; real execution requires HTTP client packages and a running Ollama 0.24.0+ service. Host `.env`/model overrides are ignored. No Hugging Face tokenizer is used; HTTP inference uses the explicitly configured local service.

The fixed input is `scripts/evaluation/fixtures-v1.json`; output is a new private `.local/model-evaluations/<time>-run-<random>/report.json`. Default mode creates only reports and reads local Git/dependency metadata. Real mode invokes the independent Ollama service, uses one owned supervised HTTP client worker and never downloads, installs, starts services or touches AWS/cluster resources. Model output is neither logged nor exported by default.

Exit code 0 means either a valid `not_run` plan or a real `passed` result; check the
report to distinguish them. Exit codes 1, 2 and 3 mean failure, invalid arguments and
pending human review, respectively. Complete semantic acceptance requires the explicit
terminal judgments. Read the [evaluation guide](../testing/model-evaluation.md) for
rules, commands, resource budgets and privacy; historical reproduction limitations
belong to the [material audit](../reports/model-evaluation-materials-2026-09-25.md). The
shared gate runs only `test_model_evaluation.py` with doubles, never this real-inference
entry.

## Minikube public entry

`scripts/minikube_demo.sh COMMAND --profile NAME` resolves the checkout from its own
path and executes `minikube_helm.py` using `REVIEW_PYTHON` or the backend virtual
environment. This is the maintained Helm CLI, implemented in `deployment/helm/cli.py`.
It offers `init`, `doctor`, `up`, `status`, `logs`, `port-forward`, `verify`, `undeploy`
and `rollback`; see the [Helm deployment guide](../guides/minikube-demo.md).

### Explicit legacy entry

`scripts/minikube_demo.sh legacy COMMAND [OPTIONS]` dispatches to the legacy runtime.
Direct `python scripts/minikube_demo.py COMMAND` remains a legacy compatibility entry.
The following command table and options describe this legacy path. Operational commands require macOS/Linux, Python dev dependencies, local Docker, minikube and kubectl. Help parses before discovery; `stop` prints advice and performs no external operations.

| Command | Preconditions and actual effects |
| --- | --- |
| `doctor` | Discover/select a running target and read resource/version/storage diagnostics; temporary private kubeconfig; reads Docker/cluster and may execute diagnostic commands in the owned backend; no resource mutations. Nonzero for failed or missing diagnostics |
| `up` | Authorization required: mandatory target/ownership/native-architecture/storage checks, fresh node-side host Ollama IPv4 resolution and generated `/32:11434` backend egress, private target state/lock, advisory diagnostics, native image build/reuse/load, namespace/Secret/PVC/workload changes, local users table initialization and readiness waits; model download may follow. Does not start/resize clusters, rotate existing key or push images |
| `verify` | Authorization required: matching completed deployment/build evidence before HTTP/account/review writes; real inference, isolation checks, controlled workload recreation and persistence checks. Produces report and private acceptance credentials |
| `port-forward` | Completed deployment record and saved port; creates an owned foreground loopback kubectl child and cleans it up on exit; no unknown process termination |
| `status` | Existing valid ownership; reads selected resources/CNI, opens target operation lock; no workload changes |
| `logs` | Existing valid ownership; reads and filters structured safe backend events; local lock, no workload changes |
| `import-state` | Verify the selected live target and legacy ownership, copy privately under old/shared locks, preserve original records; no cluster mutations |
| `undeploy` | Require trusted ownership and an idle, non-draining backend; fence ingress/admission, delete owned runtime resources with UID/version preconditions; preserve data by default. Explicit confirmed purge additionally deletes owned PVCs/Secret; namespace remains |
| `inspect-target` | Explicit profile; verified private target connection; print public cluster/namespace/owner identities without importing state or changing resources |
| `recover-cleanup` | Explicit profile and expected cluster/namespace/owner identities; read-only preview by default. Confirmed `--execute --purge-data --confirm-data-loss local-review-demo` uses a complete inventory and queue fence before conditional full cleanup; independent private recovery journal |
| `stop` | Compatibility advice only, exit zero; does not stop forwarding, workloads or minikube |

Options:

- `--profile NAME` selects an existing running profile; required when multiple profiles are running.
- `--minikube-home PATH` overrides `MINIKUBE_HOME` / `~/.minikube`.
- `--storage-class NAME` selects an existing minikube-hostpath class for `up` (default `standard`).
- `--port N` selects the localhost HTTP port for `up` (default 8080; 1024–65535).
- `--cold-timeout N` defaults to 3600 seconds, `--warm-timeout N` to 600 (minimum 60).
- `--skip-restart` makes `verify` partial and nonzero.
- `-h/--help` prints help.

No force/skip-ownership option exists. Runtime errors exit 1; argparse errors exit 2; interruption exits 130.

Target records are stored in the following directory:
`${XDG_STATE_HOME:-$HOME/.local/state}/local-qwen-demo/targets/<profile>-<home-hash>/<cluster-uid>/`
contains `owner.json`, `plan.json`, `startup.json`, `deployment.json`,
`verification.json`, `undeployment.json`, independent `recovery-cleanup.json`,
`kubeconfig`, private `acceptance-account.json` and archived `attempts/`. Shared locks
live in `<state-root>/locks/`. `LOCAL_QWEN_STATE_HOME` or `--state-root` overrides the
root. `--from-state` supplies an explicit legacy checkout/state source. `undeploy
--purge-data --confirm-data-loss local-review-demo` explicitly deletes owned data;
`--delete-timeout` bounds waits (1–600 seconds, default 120). Reports distinguish
diagnostic findings, readiness, review completion, persistence and UI acceptance. Never
share credential files or manufacture missing image proof. See [recovery and
acceptance](../guides/minikube-legacy.md) for the detailed procedure.

Lost-state recovery additionally requires `--expect-cluster-uid`, `--expect-namespace-uid` and `--expect-owner`; `--restore-frontend` is a separate identity-protected restoration action. Recovery never imports state automatically. Read the [lost-state recovery procedure](../operations/minikube-lost-state-recovery.md) before using these options.

Legacy implementation responsibilities:

| Package module | Responsibility |
| --- | --- |
| `legacy/cli.py` | Parse legacy arguments, choose the target, acquire its lock and dispatch |
| `legacy/runtime.py` | Safe subprocess/resource operations and existing runtime state |
| `legacy/context.py` | Bind and restore the operation context used by helpers |
| `legacy/target.py`, `capacity.py`, `build.py` | Discovery, mandatory requirements, advisory resource budgets and image reuse |
| `legacy/workflow.py` | Deployment stages and replica restoration |
| `legacy/state.py`, `store.py` | Ownership/image evidence, private paths, target locks and imports |
| `legacy/verify.py` | Real acceptance orchestration under target and ownership guards |
| `legacy/ollama.py` | Node-side host resolution and exact `/32` egress |
| `legacy/undeploy.py`, `recovery.py` | Queue fencing, identity-conditional deletion and recovery journals |
| `legacy/helm_deploy.py` | Earlier direct Helm installer retained through `scripts/helm_deploy.py` |

The CLI selects the target before running a command. Diagnostics, import and cleanup
use the same target lock across checkouts. `up` enforces mandatory checks independently
of `doctor`'s diagnostic verdict. The `minikube_*.py` helper files at the root are import
compatibility wrappers; all implementation changes belong in these packages.

## Verification entry chain

`scripts/check.sh` runs backend lint/tests, frontend lint/tests/build, then
`scripts/check_scripts.sh`. These delegate to `checks/all.sh` and `checks/scripts.sh`.
The focused deployment entry delegates to `checks/deployment.sh`; the Linux CI schema
entry delegates to `checks/schema.sh`. Script lint runs once in the script gate,
including shared code and tests. The script gate recursively syntax-checks Shell files, runs all
deployment, release/GitOps, inference, evaluator and local-initializer tests, then
validates the actual minikube render and both Helm charts. Tests use temporary state, API doubles and
loopback servers; no real cluster or model is needed.

Browser tests are separate locally; CI also runs them with the fake-model harness.
Pull-request CI runs application and configuration checks. After a merge to `main`,
the release workflow reruns those checks, builds changed images and publishes them to
GHCR before proposing a deployment snapshot. Those release steps are separate from
`check.sh`; CI has no cluster access. See the [GitOps guide](../guides/gitops.md) and
[test matrix](../testing/README.md#test-matrix) for deployment and acceptance boundaries.
