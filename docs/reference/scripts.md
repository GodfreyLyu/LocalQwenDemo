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
    common/               # Target, forwarding, queue guard, acceptance, files and units
    helm/                 # CLI, session, build, diagnostics, lifecycle and verification
  validation/             # Resource invariants, snapshots, Helm and GitOps validation
  release/                # Snapshot generation, publishing, migration and GitHub requests
  evaluation/             # CLI, configuration, metrics, quality, reports and worker supervision
  tests/                  # Domain regressions and cross-domain tooling contracts
  tooling_paths.py        # Checkout paths and backend import preparation
```

`deployment/common` does not import the Helm CLI or session. Shared helpers receive
command builders, environments or executors from their callers; target ownership and
release-specific deletion rules remain in the Helm implementation.

Helm and GitOps validators share `validation/resources.py`; snapshot orchestration
lives in `validation/snapshot.py`. Neither validator imports the other's CLI.
Release commands share GitHub request encoding in `release/github.py`, while callers
retain their own transport and failure policies. Evaluator reports fingerprint all
implementation modules as well as the public entry and shared path bootstrap.

## Local commands and checks

| Entry / invocation | Inputs, options and environment | Dependencies | Output, generated files and effects |
| --- | --- | --- | --- |
| `bash scripts/check.sh` | No options; do not export `RUN_REAL_MODEL=1` for routine checks | Python dev lock, npm dependencies, Git, kubectl, Helm | Lint/format, backend tests, frontend lint/tests/build, local script regressions, Helm structural checks; caches and `frontend/dist`; fail-fast nonzero; no install/build-image/deploy |
| `scripts/check_scripts.sh` | `REVIEW_PYTHON` overrides the default interpreter; runs all script domains, Helm validation | Python dev lock, Bash, Git, kubectl, Helm | Shell syntax includes all maintained entry points; temporary test files/loopback children only; no real cloud/cluster writes |
| `scripts/check_minikube_demo.sh` | `REVIEW_PYTHON` overrides default `backend/.venv/bin/python` | Ruff, pytest, HTTPX, PyYAML, kubectl, Helm | Lint/format and all tests under `scripts/tests/deployment`; real offline render and loopback socket/process tests; no cluster |
| `backend/.venv/bin/python scripts/local_demo.py --fake-model` | `--fake-model` opt-in; **default uses real model**; `--port` default 8000; `--data-dir` default `.local/demo` | Dev lock; model client lock and local Ollama when real | Loopback API; Moto accounts reset and random signing key changes on restart; persistent SQLite in data dir; real mode uses `--ollama-base-url` (default localhost:11434) and uses Ollama tokenization |
| `backend/.venv/bin/python scripts/init_local_users.py` | No CLI parser/options; required explicit `DYNAMODB_ENDPOINT_URL`; only loopback with an explicit port allowed; fixed dummy credentials | boto3, running DynamoDB Local | Describes and idempotently creates `llm-review-users`; **local database write**. Do not invoke with `--help`: it is not a help-capable script |

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

The shell entry forwards arguments to `minikube_helm.py`, which calls
`deployment.helm.cli.entry()` and `main()`. Target discovery lives in
`deployment/common/target.py`; `Session` handles local records and locking.
`build.py` and `diagnostics.py` own builds and diagnostics, `lifecycle.py` owns safe
shutdown/removal, and `verify.py` owns acceptance. The retired `legacy` subcommand
and direct legacy Python entries are no longer available.

Every operational command requires `--profile NAME`. Use `--namespace` and `--release`
consistently; `-f` supplies values, `--state-root` selects private local evidence, and
`--help` lists the supported options. `init` prepares a namespace and signing Secret;
`up` builds and loads images before Helm installation or upgrade. `verify` performs
real review and persistence checks. `undeploy` preserves data by default; confirmed
purge remains explicit. Argo CD instances must be managed through their GitOps source.

## Verification entry chain

`scripts/check.sh` runs backend lint/tests, frontend lint/tests/build, then
`scripts/check_scripts.sh`. These delegate to `checks/all.sh` and `checks/scripts.sh`.
The focused deployment entry delegates to `checks/deployment.sh`; the Linux CI schema
entry delegates to `checks/schema.sh`. Script lint runs once in the script gate,
including shared code and tests. The script gate recursively syntax-checks Shell files, runs all
deployment, release/GitOps, inference, evaluator, tooling and local-initializer tests, then
validates both Helm charts. Tests use temporary state, API doubles and
loopback servers; no real cluster or model is needed.

Browser tests are separate locally; CI also runs them with the fake-model harness.
Pull-request CI runs application and configuration checks. After a merge to `main`,
the release workflow reruns those checks, builds changed images and publishes them to
GHCR before proposing a deployment snapshot. Those release steps are separate from
`check.sh`; CI has no cluster access. See the [GitOps guide](../guides/gitops.md) and
[test matrix](../testing/README.md#test-matrix) for deployment and acceptance boundaries.
