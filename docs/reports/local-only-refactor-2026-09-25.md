# Local-only deployment refactor — 2026-09-25

## Scope and boundaries

The maintained product is a local CPU-based LLM code-review service with automated
deployment to an existing minikube cluster. This change edits repository source and
runs offline checks only. No AWS API, live cluster operation, container image build,
real model inference, state import, deployment or data cleanup was performed.

The starting worktree was clean. Existing shared-state, cross-checkout locking,
migration, undeploy and lost-state recovery implementations were retained. Hash checks
confirmed the model, cache validation, startup, coordinator, SQLite storage, authentication,
target/state/store/verify/undeploy/recovery modules and original A/B JSON evidence were
unchanged. The CLI's local table inspection now shares the restricted local transport.

## Removed and retained dependencies

Removed 46 tracked cloud-only files: Terraform infrastructure/provider locks and tests,
EKS overlay and cloud secret integration, cloud deploy/destroy/render/eligibility/secret
scripts and their fixtures, deployment workflow, and the cloud deployment runbook.
Cloud-only sections of current architecture, observability, security, cost and operations
guides were retired. Mixed historical reports retain original observations and limitations;
only archival notices and retired navigation were changed.

Ignored operator state, provider caches, credentials, model files, application data and
existing logs were not deleted. Legacy Terraform ignore rules remain to prevent accidental
publication of such files. Removing tracked infrastructure does not destroy external resources.

Retained dependencies/references have specific local purposes:

- `amazon/dynamodb-local:3.1.0`, boto3/botocore and their required transitive dependencies
  (including s3transfer) support the local account API. No direct Python dependency was
  found to be exclusively for cloud deployment; runtime/model lock versions are unchanged.
- `AWS_REGION` supplies SDK signing metadata; literal invalid local credentials, disabled
  metadata/shared configuration, explicit endpoint and empty proxy configuration prevent
  runtime cloud fallback. Host credential/profile/web-identity/container sources cannot
  select the account transport. Missing, malformed or nonlocal endpoints fail before SDK use.
- Moto and test-only SDK clients provide in-memory protocol emulation. Cloud endpoint/header
  strings in regression tests verify rejection or untrusted-input behavior.
- Remaining current documentation references describe safety boundaries. Cloud observations
  in dated mixed reports are archival evidence, not supported operating instructions.

Local frontend/API development, Compose, the fake-model browser harness and pull-request
local image-build CI remain useful. CI never pushes an image or deploys a cluster. Image
builds were not executed during this refactor.

## Offline validation

| Check | Actual result |
| --- | --- |
| Backend pytest | 157 passed; 1 opt-in real-model test skipped |
| Minikube regression suites | 319 passed |
| Evaluator and local table initializer regressions | 45 passed |
| Frontend Vitest | 15 passed |
| Playwright, deterministic fake model + Moto | 2 passed; isolated temporary history/artifacts |
| Python Ruff lint/format and syntax | Passed; 40 Python files |
| Shell syntax, frontend ESLint/Prettier, TypeScript/Vite build | Passed |
| Actual minikube Kustomize render | 17 resources validated; byte-identical to pre-refactor render |
| Local Markdown file/anchor references | No broken links found |
| Removed-file references in current code/docs | No stale operational references found |
| Whitespace check | Passed |

The first aggregate run exposed a retired EKS resource-count assertion (318 other
minikube tests passed). It was replaced with the maintained minikube manifest contract,
and both the script gate and final complete `bash scripts/check.sh` passed on rerun. Endpoint tests inspect real SDK request construction
with an in-memory transport, including hostile host credential, endpoint and proxy settings;
they do not call AWS. Initialization tests exercise real SDK serialization with stubbed
responses and verify schema conflicts leave existing data intact.

Known non-failing warnings: two backend dependency deprecations (Starlette/httpx and
AnyIO BlockingPortal), Vite's large-bundle warning, and browser test color-environment
warnings. No dependency or bundle tuning was folded into this refactor.

## Migration implications and unexecuted acceptance

An old `ENVIRONMENT=production` configuration is intentionally rejected. Native local
configuration must supply an explicit allowed DynamoDB Local endpoint. Existing minikube
configuration already satisfies this requirement. The same minikube render preserves
OMP=2, model/revision/BF16, CPU threads, generation budget, timeout, resources, security
contexts and all three PVCs. Prompts, quality gates and product state machines are unchanged.

Backend build inputs changed, so an old image/deployment record is not evidence for this
code. A separately authorized normal `up` must build/load the new source with the existing
fingerprint and image identity checks. Do not edit prior fingerprints or acceptance records.
Existing accounts/data must remain subject to the normal ownership and queue guards.

Live application readiness, real completed review, persistence acceptance, full minikube
`verify`, deployed-browser acceptance, and runtime cleanup remain **not_run for this change**.
The fake-model browser results above cannot satisfy any real-model/minikube acceptance.
Follow the current [README](../../README.md) and [minikube guide](../guides/minikube-demo.md)
for independently authorized operations. There is no automatic follow-up deployment.
