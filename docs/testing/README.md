# Testing and acceptance

[Documentation index](../README.md)

The maintained deployment runs on an existing Minikube cluster and uses an
independent Ollama service exclusively.
Offline tests exercise product behavior and deployment safety without a cluster,
model download or real inference. Operational acceptance requires separate authorization and real results.

## Strategy and discovery

The repository-root `pytest.ini` owns discovery, import paths and marker registration
for both Python suites. A bare `backend/.venv/bin/pytest` from the repository root
collects `backend/tests` and `scripts/tests`. Pass a directory explicitly for a focused
run; this also applies when invoking pytest from `backend`. Imports use importlib mode with namespace-package resolution, so domain directories
may reuse basenames and multiprocessing workers can import their test doubles.

Tests stay with their owning components and are grouped by domain:

```text
backend/tests/
  api/          # Authentication, review workflows, health, logging and HTTP contracts
  core/         # Configuration and rate limiting
  inference/    # Prompts/output, Ollama transport, coordinator and opt-in model smoke
  persistence/  # SQLite, local DynamoDB transport and startup
  support.py    # Model double and API helpers; fixtures live in conftest.py
scripts/tests/
  deployment/   # Helm rendering/lifecycle, ownership, state and shared process guards
  release/      # Planning, publishing, GitOps and chart contracts
  inference/    # Ollama runtime doubles and separate chart tests
  evaluation/   # Evaluator behavior using model doubles
  persistence/  # Local users initializer
  tooling/      # Public CLI compatibility and package dependency boundaries
  support/      # Shared paths, deployment helpers, release helpers and Ollama double
frontend/src/
  tests/        # Authentication, reviews, history, runtime and Markdown behavior
  test-support/ # Shared workspace responses and editor double
frontend/e2e/   # Real browser/API with fake inference and Moto accounts
```

Each Python test has exactly one primary level marker. Choose it by the boundary the
test verifies, not by its filename or by whether any mock is present:

| Marker | Boundary |
| --- | --- |
| `unit` | Isolated logic with controlled collaborators |
| `component` | Collaborating application modules with external-service doubles; API tests may use temporary SQLite internally |
| `integration` | Real storage, filesystem, process, socket or CLI boundary under test |
| `e2e` | Reserved for complete deployed journeys; current browser tests use Playwright's separate runner |

Dependency markers are additive: `requires_helm`, `requires_kubectl`, `requires_git`,
`loopback`, `real_model` and `requires_cluster`. `loopback` means test-owned sockets;
`requires_kubectl` can mean offline rendering or a test-owned HTTP API, not a live
cluster. `security`, `contract` and `recovery` are cross-cutting concern markers and
can coexist with any primary level. Unknown markers fail collection.

Examples from the repository root:

```bash
# Fast logic checks; does not execute Helm, kubectl, browsers or real inference.
backend/.venv/bin/pytest -m unit
# All API behavior, or security checks across both Python suites.
backend/.venv/bin/pytest backend/tests/api
backend/.venv/bin/pytest -m 'security and not real_model and not requires_cluster'
# All Python tests that do not need a real model or live cluster.
backend/.venv/bin/pytest -m 'not real_model and not requires_cluster'
# A focused subset without local CLI/socket dependencies.
backend/.venv/bin/pytest -m 'not requires_helm and not requires_kubectl and not requires_git and not loopback and not real_model and not requires_cluster'
```

The real-model smoke remains opt-in through `RUN_REAL_MODEL=1`; the ordinary check
scripts also exclude `real_model` and `requires_cluster` explicitly, even if an operator
has exported that variable. Skipped or deselected inference tests are not model passes.
No test may weaken ownership or security just to pass.

### Shared fixtures and test organization

`backend/tests/conftest.py` owns the application factory; ordinary helpers are imported
from `backend.tests.support`, never from `conftest`. Release fixtures live in
`scripts/tests/release/conftest.py`, and release helpers in `scripts.tests.support.release`.
Tests must not import helpers or fixtures from another `test_*.py` module.

Every test under `scripts/tests/deployment` receives an isolated `LOCAL_QWEN_STATE_HOME`
from that directory's autouse fixture. This isolation no longer depends on a filename
prefix. Shared repository paths come from `scripts.tests.support.paths`. Tests import canonical packages such as
`deployment.helm.session`, `release.publisher` and `evaluation.runner`; only public
entry compatibility tests import or execute the entry point wrappers. See the
[script package map](../reference/scripts.md#organization-and-dependency-boundaries).

When moving tests, compare collected test names and parameter IDs before and after the
move, not just totals. Retiring Kustomize removes its dedicated tests; shared forwarding,
SQLite admission fencing, acceptance and image-permission coverage remains in the Helm
suite. The rendered ConfigMap-to-Settings test lives there too, where Helm is available.
Historical reports retain their original paths and counts.

## Test matrix

| Surface | Entry | What it establishes |
| --- | --- | --- |
| Backend | `backend/.venv/bin/pytest backend/tests -m 'not real_model and not requires_cluster'` | Queue/capacity/idempotency/restart, authentication, Cookie/CSRF, isolation, Ollama/context/startup/logging contracts; Moto accounts and model doubles |
| Local transport | Backend local-DynamoDB tests | Missing/remote endpoints rejected; SDK host credentials/profiles/metadata/proxies cannot replace local transport |
| Minikube | `scripts/check_minikube_demo.sh` | Helm lifecycle, resource ownership, forwarding, queue fencing, storage retention and purge; simulated cluster APIs and real loopback fixtures |
| Release and charts | Shared script gate | Helm rendering, GitOps, snapshot/provenance and simulated publishing with local Git remotes |
| Local initializer | Shared script gate | Idempotent create/reuse, ACTIVE schema checks, local-only endpoints, no data replacement |
| Evaluator | Shared script gate | Model doubles, report states, privacy and fixed synthetic-suite contracts; no model load |
| Frontend | `npm --prefix frontend test` | UI/API handling and rendering with test doubles |
| Manifest invariants | `backend/.venv/bin/python scripts/validate_helm.py` | Helm-rendered model/resources/security/PVC/local dependency contracts |
| Browser harness | `npm --prefix frontend run test:e2e` | Real local browser/API/SQLite with deterministic fake model and Moto; not real-model minikube acceptance |

## Suite ownership

Tests stay with their components. Script tests use private temporary state and fake
API fixtures. Real socket
fixtures test port conflicts, owned-process cleanup and conditional kubectl DELETE
transport against loopback servers. They must never discover or modify a live cluster.
Local database tests use AWS SDK/Moto interfaces to emulate the protocol and check
that calls cannot fall back to real cloud services.

## Quick check

After installing the [development dependencies](../guides/local-development.md):

```bash
backend/.venv/bin/pytest backend/tests -m 'not real_model and not requires_cluster'
npm --prefix frontend test
scripts/check_scripts.sh
```

## Local complete check

Install the Python 3.12 virtual environment from the dev lock, the editable backend
package and Node 24/npm dependencies. The gate also requires Git, Helm and kubectl
for local repositories and offline rendering and permission to bind temporary loopback test sockets. These checks need no
Docker daemon, Minikube cluster, cloud account or infrastructure providers. Installing
dependencies requires network access, but the tests do not download weights or images.

```bash
bash scripts/check.sh
```

This runs Ruff lint/format, backend pytest, frontend lint/Vitest/build, Shell syntax,
all maintained script regressions (including release, GitOps and Ollama charts),
manifest validation and Helm lint/render validation. It fails on the first failed
stage. It never builds container images, deploys, migrates actual state or invokes real
inference. Do not export `RUN_REAL_MODEL=1` during routine checks.

Browser checks remain a separate entry point: `npm --prefix frontend run test:e2e`.
They are not part of `scripts/check.sh`. Real-model quality evaluation and cluster
acceptance below are separate from both offline and fake-model browser checks.

The `Quality checks` workflow runs on pull requests to `main`, manual dispatch and
calls from other workflows. It uses the same `scripts/check_scripts.sh` entry point
for every script regression and offline configuration check, and separately runs the
fake-model browser suite.
Its `configuration` job adds Kubernetes schema checks using a downloaded, verified
Linux tool; that network-dependent CI check is outside the local offline gate.

After a merge to `main`, `Prepare release` reruns quality checks, builds changed
backend, frontend and Ollama images, and publishes them to GHCR. It proposes a reviewed
deployment snapshot; Argo CD reconciles approved snapshots in the cluster. CI does not
access the cluster or establish real-model acceptance. See the [GitOps guide](../guides/gitops.md).

## Explicit real-model smoke and quality evaluation

The opt-in [model smoke](../reference/model.md) and [fixed-suite evaluator](model-evaluation.md)
call the configured Ollama service, which loads model weights and uses CPU, GPU, and
memory resources as available. For comparable runs, keep the model digest, quantization,
prompts, sampling settings, budgets, quality rules, and service configuration fixed.
Evaluation plans have status `not_run`; they are not successful reviews. The default
check suite does not run real inference.

## Authorized environment acceptance

Follow the [local Helm guide](../guides/minikube-demo.md) for the development CLI or
the [release guide](../guides/helm-release.md) for published deployments. The CLI
command `up` establishes only readiness. `verify` requires records that match the
deployed build and uses normal
authentication. It must complete a real review that passes the quality checks, then
verify account isolation and persistence. It may create accounts, write reviews and
restart owned workloads. Do not run a full verify just to inspect a performance problem.
Stop your owned foreground port-forward before verify uses its port. Browser acceptance
through the deployed same-origin entry is a separate check.

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
- Ready: dependencies and startup validation passed; review completion is checked separately.
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
