# Shared minikube state and undeploy: offline delivery

Implementation and offline validation completed on 2026-09-25. No real user state
was imported, no cluster resources were changed, and no review was submitted.

- The default user-level state root is independent of the checkout. Target identity,
  random ownership markers, private permissions, shared locks and image/source proof
  remain separate from historical checkout provenance.
- Legacy import was exercised in temporary directories, including interruption,
  repeated import, conflicting targets and the old/new operation locks. Original
  records and credentials were not rewritten as fresh deployment or acceptance proof.
- Default teardown preserves data. Explicit data purge retains the namespace and
  ownership anchor; namespace deletion is intentionally not implemented.
- Teardown tests cover idle/draining rejection, SQLite write fencing against a racing
  submission, UID/version preconditions, replacement resources, unknown resources,
  deletion timeouts, failure recovery, repeated absence and invalidation of old success.
- Real kubectl was pointed only at a disposable loopback HTTP fixture to verify that
  raw DELETE transmits the UID/resourceVersion DeleteOptions body. This is not a test
  against the real Kubernetes API or its garbage collector.

Validation: `scripts/check_minikube_demo.sh` passed **257 tests**; Ruff lint/format and
shell syntax checks passed. Python parsing passed. Kustomize rendered **17 resources**,
with the backend OpenMP and resource contracts unchanged. `git diff --check` passed;
backend, frontend and deployment manifests have no changes in this delivery.

Runtime import, admission fencing against the deployed backend, Kubernetes foreground
cleanup/finalizers, retained-data redeployment and full verify/browser acceptance remain
**not_run** for this delivery. A readiness probe under the temporary SQLite reservation
is intentionally unavailable; cleanup cannot proceed unless it can distinguish that
storage contention from a draining/unhealthy worker. Runtime operators must inspect a
failed cleanup journal and restore with up when idle-shutdown proof is missing.

Use the [operator guide](../guides/minikube-demo.md#state-and-ownership-protection) for
the import and cleanup commands. Do not infer actual cleanup or acceptance success from
these offline results.
