# Testing and acceptance

[Documentation index](../README.md)

The maintained deployment runs on an existing Minikube cluster, with a separate
generic Thrift service supervising its own Ollama child. Offline tests check application behavior and deployment
tooling without a live cluster, model download or real inference. Real-model and
deployment acceptance are separate checks.

## Strategy and discovery

The repository-root `pytest.ini` owns discovery, import paths and marker registration
for the backend and script suites. A bare `backend/.venv/bin/pytest` from the repository root
collects `backend/tests` and `scripts/tests`. Pass a directory explicitly for a focused
run; this also applies when invoking pytest from `backend`. Importlib mode and
namespace-package resolution let test directories reuse filenames and let
multiprocessing workers import their test doubles.

The independent service uses `inference-service/pytest.ini` and its own virtual
environment. Java unit tests use Maven/JUnit. Neither is collected by the root
pytest defaults; the complete check invokes the separate inference gate.

Tests stay with their owning components and are grouped by domain:

```text
backend/tests/
  api/          # Authentication, review workflows, health, logging and HTTP contracts
  core/         # Configuration and rate limiting
  inference/    # Prompts/output, RPC doubles, coordinator and opt-in real reviews
  persistence/  # SQLite, local DynamoDB transport and startup
  support.py    # Model double and API helpers; fixtures live in conftest.py
scripts/tests/
  deployment/   # Helm rendering/lifecycle, ownership, state and shared process guards
  release/      # Planning, publishing, GitOps and chart contracts
  inference/    # Ollama runtime doubles and separate chart tests
  monitoring/   # Monitoring charts, platform configuration and PromQL scenarios
  evaluation/   # Evaluator behavior using model doubles
  persistence/  # Local users initializer
  tooling/      # Public CLI compatibility and package dependency boundaries
  support/      # Shared paths, deployment helpers and release helpers
inference-service/tests/
                # Generic RPC registry, native HTTP doubles, process lifecycle and Java interop
clients/java/src/test/java/
                # JUnit TCP tests and a Java probe for the Python interop fixture
clients/java/examples/consumer/
                # Independent Maven application with only the SDK dependency
frontend/src/
  tests/        # Authentication, reviews, history, runtime and Markdown behavior
  test-support/ # Shared workspace responses and editor double
frontend/e2e/   # Real browser/API with fake inference and Moto accounts
```

Each backend or script test has exactly one primary level marker. Choose it by the boundary the
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

Run fast logic checks without Helm, kubectl, browsers or real inference:

```bash
backend/.venv/bin/pytest -m unit
```

Check API behavior, or run security checks across both Python suites:

```bash
backend/.venv/bin/pytest backend/tests/api
```

```bash
backend/.venv/bin/pytest -m 'security and not real_model and not requires_cluster'
```

Run backend and script tests that do not need a real model or live cluster:

```bash
backend/.venv/bin/pytest -m 'not real_model and not requires_cluster'
```

To exclude local CLI and socket dependencies as well:

```bash
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
from that directory's autouse fixture. Shared repository paths come from
`scripts.tests.support.paths`. Tests import canonical packages such as
`deployment.helm.session`, `release.publisher` and `evaluation.runner`; only public
entry compatibility tests import or execute the entry point wrappers. See the
[script package map](../../scripts/README.md#organization-and-dependency-boundaries).

When moving tests, compare collected test names and parameter IDs before and after the
move, not just totals. Historical reports retain their original paths and counts.

Script tests use temporary state and fake APIs. Loopback servers check port conflicts,
cleanup of owned processes and conditional kubectl DELETE requests; they must never
discover or modify a live cluster. Local database tests use AWS SDK/Moto interfaces
and verify that requests cannot fall back to real cloud services.

## Test matrix

| Surface | Entry | What it establishes |
| --- | --- | --- |
| Backend | `backend/.venv/bin/pytest backend/tests -m 'not real_model and not requires_cluster'` | Queue/capacity/idempotency/restart, authentication, Cookie/CSRF, isolation, prompts/budgets/output, RPC recovery, startup and logging; Moto accounts and model doubles |
| Local transport | Backend local-DynamoDB tests | Missing/remote endpoints rejected; SDK host credentials/profiles/metadata/proxies cannot replace local transport |
| Backend Thrift adapter | `backend/.venv/bin/pytest backend/tests/inference/test_thrift.py` | Application prompts, budgets, errors, metrics and uncertain-execution handling with an injected RPC double |
| Independent inference and Java | `bash scripts/checks/inference.sh` | Binding drift, real TCP client/processor tests, registry deadlines/cancellation/retention, native HTTP doubles, child lifecycle, Maven SDK tests and a standalone consumer |
| Java SDK | `mvn -f clients/java/pom.xml test` | Real loopback Java peers check RPC values, errors, connection cleanup, read timeouts, strict framing and concurrent callers |
| Real Thrift review | `RUN_REAL_MODEL=1 THRIFT_HOST=127.0.0.1 backend/.venv/bin/pytest backend/tests/inference/test_real_model.py::test_real_fastapi_review_persists_remote_identity` | Opt-in FastAPI → TCP Thrift → an already running inference service → real Ollama, with saved model provenance |
| Minikube | `scripts/check_minikube_demo.sh` | Helm lifecycle, resource ownership, forwarding, queue fencing, storage retention and purge; simulated cluster APIs and real loopback fixtures |
| Release and charts | Shared script gate | Helm rendering, GitOps, snapshot/provenance and simulated publishing with local Git remotes |
| Local initializer | Shared script gate | Idempotent create/reuse, ACTIVE schema checks, local-only endpoints, no data replacement |
| Evaluator | Shared script gate | Model doubles, report states, privacy and fixed synthetic-suite contracts; no model load |
| Monitoring | Shared script gate; `scripts/check_monitoring.sh` in CI | Metrics, platform and chart contracts; CI also checks alert rules and dashboard queries with promtool |
| Frontend | `npm --prefix frontend test` | UI/API handling and rendering with test doubles |
| Manifest invariants | `backend/.venv/bin/python scripts/validate_helm.py` | Helm-rendered model/resources/security/PVC/local dependency contracts |
| Browser harness | `npm --prefix frontend run test:e2e` | Real local browser/API/SQLite with deterministic fake model and Moto; not real-model minikube acceptance |

## Quick check

After installing the [development dependencies](../guides/local-development.md):

```bash
backend/.venv/bin/pytest backend/tests -m 'not real_model and not requires_cluster'
```

```bash
npm --prefix frontend test
```

```bash
scripts/check_scripts.sh
```

## Local complete check

Install both Python development environments and editable packages (`contracts` plus
their owning application), and the Node 24/npm dependencies. The gate also needs:

- Git, Helm and kubectl for temporary repositories and offline rendering.
- Apache Thrift 0.25.0 for the generated-binding drift check.
- JDK 11+ and Maven 3.9+ for the SDK, example and independent consumer.
- Permission to bind temporary loopback sockets.

Use the [local development guide](../guides/local-development.md) for the backend
and frontend, and [independent service setup](../guides/apache-thrift.md#run-the-projects-independently)
for `inference-service/.venv`. No Docker daemon, Minikube cluster, cloud account or
infrastructure provider is required when the matching Thrift compiler is installed
locally. Maven may download dependencies/plugins on the first run; tests do not
download model weights or container images. CI builds the compiler from the
official Apache Thrift 0.25.0 source archive after checking its pinned SHA-256,
with language libraries, upstream tests and tutorials disabled.

```bash
bash scripts/check.sh
```

This runs Ruff lint/format checks, backend pytest, frontend lint/Vitest/build, shell syntax,
all maintained script regressions (including release, GitOps and Ollama charts),
manifest validation and Helm lint/render validation, then the inference gate.
That last stage checks bindings, runs the independent Python tests, builds/tests/
packages the Java SDK and installs it locally for the copied consumer test. Set
`MAVEN_ARGS` to select an isolated local Maven repository if desired; see the
[SDK guide](../../clients/java/README.md#generate-test-and-package).
The full check stops at the first failed stage. It never builds container images,
deploys, migrates existing application data or runs real inference. Do not export
`RUN_REAL_MODEL=1` during routine checks.

Browser checks remain a separate entry point: `npm --prefix frontend run test:e2e`.
They are not part of `scripts/check.sh`. Real-model quality evaluation and cluster
acceptance below are separate from both offline and fake-model browser checks.

The `Quality checks` workflow runs on pull requests to `main`, manual dispatch and
calls from other workflows. It uses the same `scripts/check_scripts.sh` entry point
for every script regression and offline configuration check, and separately runs the
fake-model browser suite.
The inference stage uses JDK 11, the service's locked Python environment and the
pinned Thrift compiler. Its ARM64 image build is a separate CI job.
Its `configuration` job adds Kubernetes schema checks using a downloaded, verified
Linux tool; that network-dependent CI check is outside the local offline gate.

Monitoring regressions cover independent registries, idempotent admission, read-only
queue snapshots, safe labels, partial inference measurements, timeout/drain occupancy,
and monitoring-enabled/disabled Helm rendering. The configuration job also downloads
Prometheus 3.15.0's checksum-verified promtool, checks rendered alert rules and dashboard
queries, and runs synthetic alert scenarios. Pinned Operator schemas validate the
application's ServiceMonitor and PrometheusRule; unknown CRD kinds are not skipped.
These checks also run against deployment candidates as data using main-owned scripts.

With a local promtool binary, run:

```bash
backend/.venv/bin/python scripts/validate_monitoring.py --promtool /path/to/promtool
```

Set `PROMTOOL` to that absolute path when running
`backend/.venv/bin/pytest scripts/tests/monitoring` to include additional dashboard
query scenarios. Without it, the offline suite explicitly skips the external-tool
test; CI's separate monitoring check requires the tool and never silently skips
rule validation.

After a merge to `main`, `Prepare release` reruns quality checks. If release inputs
changed, it builds the affected images, publishes them to GHCR and proposes a
deployment snapshot for review. Argo CD reconciles approved snapshots in the cluster.
CI does not access the cluster or establish real-model acceptance. See the
[GitOps guide](../guides/gitops.md).

Release lifecycle regressions exercise real temporary Git histories with mocked
GitHub PR/status transport: exact merge/source tags, atomic tag push retries and
conflicts, historical tagging, rollback content/provenance checks, immutable retries,
and preservation of pending rollback PRs during normal candidate publication.
Workflow contracts and actionlint check the trusted main finalizer and shared
normal/rollback version sequence. These checks do not test live GitHub permissions,
GHCR retention or Argo deployment. See the [rollback runbook](../guides/release-rollback.md)
for manual publishing pauses and actual recovery acceptance.

## Explicit real-model smoke and quality evaluation

The opt-in [Thrift review tests](../reference/model.md#verification)
and [fixed-suite evaluator](model-evaluation.md) exercise real inference through an
already running service that supervises its own Ollama child. Service startup also
performs a real generic warmup before opening RPC. Ollama loads
model weights and uses CPU, GPU, and
memory resources as available. For comparable runs, keep the model digest, quantization,
prompts, sampling settings, budgets, quality rules, and service configuration fixed.
Evaluation plans have status `not_run`; they are not successful reviews. The default
check suite does not run real inference.

## Authorized environment acceptance

Follow the [local Helm guide](../guides/minikube-demo.md) for the development CLI or
the [release guide](../guides/helm-release.md) for published deployments. The CLI
command `up` establishes only readiness. Automated `verify` is currently blocked
by a [legacy model-inventory helper](../guides/minikube-demo.md#automated-acceptance-limitation)
that reads removed backend settings. It is not a usable acceptance gate for the
current Thrift configuration until that helper is migrated.

The intended verification contract requires records that match the
deployed build and uses normal authentication. It must complete a real review that
passes the quality checks, then verify account isolation and persistence. It may
create accounts, write reviews and restart owned workloads. Do not run a full verify
just to inspect a performance problem.
When the helper has been migrated, stop any foreground forward before running
`verify`, which needs the frontend port. If
the [background endpoint launcher](../../scripts/README.md#background-minikube-endpoints)
is running, stop it too:

```bash
python3 scripts/minikube_endpoints.py stop
```

Repeat any custom `--context` and `--state-dir` options used at startup. The verify
command starts its own temporary forward. Browser acceptance through the deployed
same-origin URL is a separate check.

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

## Independent inference checks

[The Thrift guide](../guides/apache-thrift.md#regenerate-test-and-build) documents
the service's environment, binding drift checks, Java SDK/example/consumer,
real TCP/processor tests, bounded registry, native HTTP doubles and real child
process tests. Run `bash scripts/checks/inference.sh` with the compiler and Java
tools above installed. `INFERENCE_PYTHON` selects the service interpreter;
`REVIEW_PYTHON` selects the interpreter for the generator script, and `MAVEN`
can select an absolute Maven executable path.

To run the service's Python tests alone, use:

```bash
inference-service/.venv/bin/python -m pytest -c inference-service/pytest.ini inference-service/tests
```

These tests do not require the backend package. Java interop tests are skipped
unless `RUN_JAVA_INTEROP=1`; the combined gate enables them and also runs Maven.
To use only the service interpreter for the gate's Python commands:

```bash
REVIEW_PYTHON=inference-service/.venv/bin/python bash scripts/checks/inference.sh
```

Real model checks remain explicit opt-in tests; image and manifest verification alone
do not establish Vulkan inference on a live krunkit node.

See the [2026-10-25 verification record](generic-llm-verification-2026-10-25.md)
for the refactor's concrete checks and deployment verification limits.
