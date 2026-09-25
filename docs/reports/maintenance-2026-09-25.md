# Documentation and check-entry maintenance — 2026-09-25

> Historical snapshot: cloud commands and deployment navigation below are retired.
> Recorded results and limitations are unchanged; use current local documentation.

Audience: reviewers and incoming maintainers. Purpose: record this bounded maintenance round, migrations and actual validation. Prerequisite: [documentation index](../README.md). Date/timezone: 2026-09-25, Asia/Tokyo. Starting revision: `d21869076ca365773ad415413f136356b445e23b`; validation includes the uncommitted maintenance changes described here. No applicable AGENTS.md was found in the project or ancestor directories. Initial Git status was clean; ignored operator files/data were retained.

## Structure and reading order

Read root README → [documentation index](../README.md) → local guide → architecture/model/security → [testing](../testing/README.md) and [scripts](../reference/scripts.md). Choose minikube or AWS procedures for the explicitly selected environment; use Reports only for dated evidence.

- `guides/`: local development and local Kubernetes procedures.
- `reference/`: architecture, API, model, security, cost boundaries, telemetry contracts and script catalog.
- `operations/`: AWS deployment, observability response, troubleshooting, backup/recovery and cleanup.
- `testing/`: discovery, matrix, quick/full/real-model/authorized acceptance paths and evidence rules.
- `reports/`: dated observations, preserved conclusions and limitations.
- `evidence/`: original JSON evidence, unchanged and unmoved.

## Migration map

Old paths below are historical names, not current links. No script or test directory moved.

| Former docs file | Current destination |
| --- | --- |
| `local-development.md` | [guides/local-development.md](../guides/local-development.md) |
| `minikube-demo.md` | [guides/minikube-demo.md](../guides/minikube-demo.md); historical diagnostics, DynamoDB/cache repairs, OpenMP summary and undated forwarding follow-up moved to [minikube maintenance report](minikube-maintenance-2026-09-20-to-21.md) |
| `architecture.md`, `api.md`, `model.md`, `security.md`, `costs-and-limitations.md` | Same basenames under `docs/reference/` |
| `aws-deployment.md` | `operations/aws-deployment.md` (retired; preserved in Git history); single maintained teardown command sequence |
| `operations.md` | [operations/recovery-and-cleanup.md](../operations/recovery-and-cleanup.md); duplicate rollout/model/log/teardown instructions replaced with links |
| `observability.md` | [reference/observability.md](../reference/observability.md) for contracts; [operations/observability.md](../operations/observability.md) for alarm procedures and live verification |
| `verification.md` | [verification-2026-09-10-to-13.md](verification-2026-09-10-to-13.md); original counts/models/findings retained |
| `minikube-inference-investigation.md` | [minikube-inference-investigation-2026-09-21.md](minikube-inference-investigation-2026-09-21.md) |
| `minikube-cpu-measurement-2026-09-21.md` | Same basename under `docs/reports/` |
| `minikube-cpu-b-measurement-2026-09-21.md` | Same basename under `docs/reports/` |

README, internal links, CLI help and Terraform alarm/dashboard runbook references were updated. Alarm descriptions now point to the operational runbook; alarm logic/settings did not change and no apply occurred. Extracted historical excerpts retain original wording; “this document/change” refers to the original recorded work, not this maintenance round.

## Script and test-entry changes

Existing public script names/options/defaults and safety checks remain intact. `scripts/check_scripts.sh` is a new thin regression entry: syntax-check Shell entries/fixtures, invoke `check_minikube_demo.sh`, then run Secret, deployment and Terraform lifecycle fixture suites. `check.sh` and the CI application job now call it; CI installs kubectl for offline rendering. `check.sh` also runs the existing dev/platform Terraform mock suites, already present in CI.

Backend pytest discovers only backend tests; it never implicitly covered `scripts/tests`. The three current Python script test files remain explicitly listed by the focused minikube gate. Frontend/browser and infrastructure tests remain with their components. Browser tests stay separate locally and retain their existing CI step; real-model and live acceptance remain opt-in.

Ruff now uses the explicit project configuration across local/CI entry points. First-party `app` import classification is independent of working directory. Five small scripts received format-only changes and one nested import block was reordered. Comments/docstrings explain target and resource ownership, attempt state, image proof, idle queue protection, subprocess deadlines/cleanup, Secret metadata/stdin handling, saved plans and teardown markers. Fixture comments explain synthetic boundaries and temporary-file/process cleanup.

Each dev Terraform test file now explicitly mocks the `aws.global` alias as well as the default AWS provider. This closes a test-isolation gap for global-region resources; it does not alter production providers or weaken assertions. No new product behavior tests were invented for documentation changes.

## Actual validation

Environment: macOS arm64; Python 3.12.14, Node 24.18.0, Terraform 1.15.8, kubectl 1.37.0/Kustomize 5.8.1. CI pins kubectl 1.35.0; local offline rendering is not a claim of cluster/client compatibility. Model: **not loaded**; production remains `Qwen/Qwen3-1.7B` at `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`.

| Check | Result and limits |
| --- | --- |
| Explicit-config Ruff lint/format | Passed for backend application/tests and scripts; 32 Python files formatted correctly |
| Backend pytest | **129 passed, 1 skipped** (opt-in real model), two dependency deprecation warnings |
| Shared script regression gate | **207 Python tests passed**, all three Shell suites passed; syntax checks included all executable Shell fixtures |
| Frontend lint, Vitest and production build | Passed; **15 tests**; existing bundle-size warning, no application changes made |
| Terraform validation | Bootstrap/backend, dev and platform all passed in a disposable source copy |
| Terraform mock tests | Dev **25 passed**, platform **1 passed**; no live resources/state/credentials |
| Terraform source format | All tracked `.tf`/`.tftest.hcl` files passed |
| Recursive format in original checkout | Nonzero due to existing ignored `infrastructure/environments/dev/terraform.tfvars`; preserved without editing |
| Manifest checks | EKS structural validator passed for 16 resources; minikube rendering exercised by regression tests |
| Syntax/help/reference checks | Python compilation, Canary JavaScript syntax, reviewed Shell helper help, Markdown paths/heading anchors and diff whitespace passed |
| Preservation checks | Original evidence bytes and public executable modes retained; model/application implementation/configuration unchanged |

The first script run in the sandbox reported 196 passed, 8 failed and 3 errors because loopback binds were prohibited (`EPERM`). The same unmodified tests passed after approved execution outside that restriction. Terraform provider schema startup also failed within the sandbox; it passed outside that restriction using installed locked providers in a temporary source copy, AWS credential/config variables disabled, `init -backend=false -lockfile=readonly`, `validate`, and only mocked plan tests. No assertion was skipped or weakened to obtain those passes.

The full `check.sh` aggregate is **not claimed green in the original checkout**: its recursive formatter encounters the ignored operator tfvars above. Relevant stages were verified separately; infrastructure ran in isolation to preserve live backend metadata. These results are local, not a GitHub Actions run.

Local evidence: `.local/verification/2026-09-25-maintenance/` contains backend log/JUnit, initial/repeated script logs, final isolated Terraform log and helper help output. Commands normally emit console output; frontend/static results were observed in this task, not all exported to standalone logs. Private runtime data and historical `.local` files were not removed.

## Not executed and follow-up boundaries

- No Terraform apply/destroy, real state plan, AWS API operation, image publication, Secret initialization/rotation, minikube/EKS deployment/restart/cleanup or real environment acceptance.
- No model inference/download, full Docker image build or browser end-to-end rerun: product/browser/server code did not change; the existing Playwright execution path was inspected and documented. Backend/build and script regressions cover the changed maintenance surfaces.
- No Git commit/push and no reference-project edits. Original raw evidence remains unchanged.
- Existing backend dependency deprecations and frontend bundle warning are recorded, not expanded into dependency upgrades or product refactoring. Review the ignored tfvars formatting separately before expecting the aggregate gate to pass in this operator checkout.
