# Testing and acceptance

[Documentation index](../README.md)

The maintained deployment is a local CPU service on an existing minikube. Offline tests
exercise product behavior and deployment safety without a cluster, model download or
real inference. Operational acceptance requires separate authorization and real results.

## Strategy and discovery

Backend pytest discovers `backend/tests`; it does not implicitly cover `scripts/tests`.
`scripts/check.sh` explicitly calls the shared script gate. Most inference tests use
model doubles; the real-model smoke test is opt-in and normally skipped. A skip is not
a real-model success. No test may weaken ownership or security just to pass.

## Test matrix

| Surface | Entry | What it establishes |
| --- | --- | --- |
| Backend | `cd backend && .venv/bin/pytest` | Queue/capacity/idempotency/restart, authentication, Cookie/CSRF, isolation, model/cache/startup/logging contracts; Moto accounts and model doubles |
| Local transport | Backend local-DynamoDB tests | Missing/remote endpoints rejected; SDK host credentials/profiles/metadata/proxies cannot replace local transport |
| Minikube | `scripts/check_minikube_demo.sh` | Real offline Kustomize render, profile/state/ownership, migration, image proof, forwarding, undeploy and lost-state recovery; simulated cluster APIs and real loopback fixtures |
| Local initializer | Shared script gate | Idempotent create/reuse, ACTIVE schema checks, local-only endpoints, no data replacement |
| Evaluator | Shared script gate | Model doubles, report states, privacy and fixed synthetic-suite contracts; no model load |
| Frontend | `npm --prefix frontend test` | UI/API handling and rendering with test doubles |
| Manifest invariants | `backend/.venv/bin/python scripts/validate_manifests.py` | Actual minikube render: model/OMP/resources/security/three PVCs/local dependency settings |
| Browser harness | `npm --prefix frontend run test:e2e` | Real local browser/API/SQLite with deterministic fake model and Moto; not real-model minikube acceptance |

## Suite ownership

Tests remain next to their components. Script tests own private temporary state and fake
API fixtures; the shared minikube lock is tested across processes/checkouts. Real socket
fixtures test port conflicts, owned-process cleanup and conditional kubectl DELETE
transport against loopback servers. They must never discover or modify a live cluster.
Local database tests retain the necessary AWS SDK/Moto names only for protocol emulation
and explicit prevention of real cloud fallback.

## Quick check

After installing the [development dependencies](../guides/local-development.md):

```bash
(cd backend && .venv/bin/pytest)
npm --prefix frontend test
scripts/check_scripts.sh
```

## Local complete check

Prerequisites: Python 3.12 virtual environment installed from the dev lock, editable
backend package, Node 24/npm dependencies, kubectl for offline rendering and permission
to bind temporary loopback test sockets. No Docker daemon, minikube cluster, cloud account
or infrastructure providers are needed for this gate. Dependency installation needs
network access; the tests themselves do not download weights or images.

```bash
bash scripts/check.sh
```

This runs Ruff lint/format, backend pytest, frontend lint/Vitest/build, Shell syntax,
all maintained script regressions and manifest validation. It fails on the first failed
stage. It never builds container images, deploys, migrates actual state or invokes real
inference. Do not export `RUN_REAL_MODEL=1` during routine checks. GitHub CI runs these
local/static surfaces, a separate fake-model browser suite and local container-build checks
on pull requests (no push); it has no deployment,
remote image push, credential federation or cloud provisioning job.

## Explicit real-model smoke and quality evaluation

The opt-in [model smoke](../reference/model.md) and [fixed-suite evaluator](model-evaluation.md)
load real weights and consume CPU/RAM. Keep model, prompts, BF16, threads, generation
parameters, budgets and quality rules fixed. Evaluation plans are `not_run`; they are
not successful reviews. No real inference is part of the default gate.

## Authorized environment acceptance

Follow [the minikube guide](../guides/minikube-demo.md). `up` establishes only readiness.
`verify` requires matching deployment/build evidence, normal authentication, a real
quality-valid completed review, isolation and persistence checks. It may create accounts,
write reviews and restart owned workloads. Do not run a full verify just to inspect a
performance problem. Stop your owned foreground port-forward before verify uses its port.
Browser acceptance through the deployed same-origin entry is a separate check.

Browser tests use a fresh temporary directory for fake history and artifacts by default.
`LOCAL_REVIEW_TEST_ROOT` can select a dedicated disposable test directory; never point it
at existing user data or logs.

## Mock boundaries and fixture maintenance

Moto mocks only the account API; it does not prove DynamoDB Local image/filesystem
behavior. Test-only transport injection never changes runtime endpoint restrictions.
Fake inference does not establish real model quality, CPU speed or memory fit. Fake
cluster APIs cannot establish CNI enforcement, scheduler capacity, native architecture,
actual image availability or successful cleanup. The pinned DynamoDB image metadata
fixture remains for Unix permission/startup regression; it is not a fresh image inspection.

## Evidence and manual acceptance

Record revision, platform/dependencies, target identity, commands, pass/fail/skip results
and limitations. Distinguish:

- `not_run`: no relevant operation was executed.
- `not_measured`: a measurement is unavailable; never assume sufficient resources.
- Failed diagnostics, resource insufficiency and actual deployment failure: separate stages.
- Ready: dependencies and startup validation passed, not a completed user review.
- Completed review: real inference and existing quality checks passed for that record.
- Persistence: retained accounts/history/cache survived the required controlled checks.
- Full verify: all required API/persistence checks passed; not browser acceptance.
- Browser acceptance: separately observed behavior through the actual deployed entry.

Save only sanitized evidence in new dated reports. Keep kubeconfigs, credentials, tokens,
user source/history, model bodies, weights and local operational state out of Git. Existing
[historical evidence](../reports/README.md) remains tied to its original context.

## Common failures

Missing packages/tools or sandbox-denied socket binds are environment blockers, not
passing tests. A port permission error is not proof of a listener. Run allowed offline
fixtures with the required loopback permissions rather than skipping assertions. Browser
binaries may require a separate install; report unavailable browser checks accurately.
Ignored operator files, existing databases, cached weights and old state are not test
fixtures and must not be rewritten or deleted by the gate.
