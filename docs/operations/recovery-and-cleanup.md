# Operations, recovery, and cleanup

[Documentation index](../README.md)

This runbook covers application startup and recovery for Helm and Argo CD deployments.
For Helm lifecycle operations, use the [Helm guide](../guides/helm-release.md); for
Argo CD, use the [GitOps guide](../guides/gitops.md).
Start with read-only diagnostics on an explicitly selected profile. Inspection does not
authorize new inference, restarts, configuration changes or deletion. Preserve data and
reports, including failed and incomplete results.

## Health and startup

```bash
scripts/minikube_demo.sh status --profile minikube
```

```bash
scripts/minikube_demo.sh logs --profile minikube
```

```bash
scripts/minikube_demo.sh doctor --profile minikube
```

These commands are for the optional Docker-driver Helm CLI. For standard Helm or Argo CD
deployments, including krunkit, inspect the workload directly with the correct context
and namespace:

```bash
kubectl --context minikube -n local-review-demo get deployments,pods,pvc
```

```bash
kubectl --context minikube -n local-review-demo logs deployment/review-backend -c review-backend --tail=100
```

The local Helm CLI commands check ownership and use the selected target's private kubeconfig
with an explicit context and namespace. Do not print kubeconfig, acceptance accounts,
Secret contents, complete environment dumps, user history bodies or raw exception strings.

`/health/live` is available during loading. `/health/ready` remains 503 until storage,
accounts and the model are ready. The local Helm CLI deployment timeout defaults to
3900 seconds and its warm verification budget to 600 seconds. The Helm profile allows
300 seconds for a whole review; direct local development defaults to 180 seconds.
A longer startup allowance does not extend the inference timeout.

### Temporary account-store connection failures

The startup `users_storage` check retries botocore `ConnectTimeoutError`,
`ReadTimeoutError`, `EndpointConnectionError` and `ConnectionClosedError`.
Other errors fail immediately, including missing tables, service `ClientError`
responses and schema mismatches. The table must be ACTIVE with a `login_id` string
hash key. This check only describes the table; the separate initializer creates it
when needed.

Retries wait 1, 2, 4 and 8 seconds, then at most 10 seconds between attempts.
The **120-second budget includes calls and waits**. Each call uses one SDK attempt,
a 3-second connect timeout and a 5-second read timeout. Calls run one at a time;
none starts at or after the deadline. An in-flight call is allowed to finish, but a
late success still fails startup. Reporting can therefore take longer than 120 seconds;
socket timeouts do not provide a hard wall-clock limit for DNS or scheduling.

During retries, liveness stays healthy, readiness returns 503 with `model_loading`,
and submissions are rejected. Success continues with queue recovery, model validation
and the final storage check. Retry exhaustion or another error enters
`startup_or_storage_failure`. Later database recovery does not restart initialization
within that process. Inspect the dependency and Pod state before deciding whether a
restart is needed. Only the account-store check retries; earlier startup stages and
inference do not repeat as part of this loop.

Shutdown interrupts retry waits and schedules no further calls. An in-flight call
uses the coordinator's shutdown grace, which defaults to 20 seconds. Cancelling the
coordinator does not forcibly stop its Python worker thread.

Safe events are `users_storage_check_started`, `users_storage_retry_scheduled`,
`users_storage_ready`, `users_storage_retry_exhausted`, and
`users_storage_check_stopped`. They include `stage: users_storage`,
`startup_attempt`, `startup_elapsed_ms`, and, when retrying, `startup_wait_ms` and
`exception_type`. Final failures also emit the existing `coordinator_failed` event.
These logs omit exception text, endpoints, credentials and account data.

| Symptom | Evidence and safe next step |
| --- | --- |
| No running profile or API unavailable | Start/fix the cluster yourself, then rerun diagnostics. The script never starts or recreates a cluster. |
| Pending backend, resource warnings | Inspect actual allocatable, requests/limits and usage, host swap, Docker capacity and disk. Do not treat unavailable metrics as sufficient capacity. |
| `Unable to access jarfile DynamoDBLocal.jar` | Verify the pinned image and effective identity. The minikube configuration uses image UID 1000, group/fsGroup 10001 and a root-only volume-permission initializer limited to the PVC root. Do not recursively change data permissions or replace the PVC. |
| Model download or validation failure | Check Ollama service readiness, the selected model digest/context and safe startup errors. Only Ollama downloads model files; a completed download alone does not prove the backend can use the model. Preserve its model PVC and partial downloads. |
| Account dependency unavailable | Confirm the local endpoint, dummy credentials, disabled SDK metadata/shared configuration and ACTIVE table/schema. The initializer is idempotent and refuses remote endpoints or schema replacement. |
| 415, 403 or 401 | POST/PUT/PATCH require JSON; Origin must match the configured scheme, host and port, and authenticated writes require the session's CSRF token. Use the documented localhost URL and sign in normally. |
| `inference_timeout` | Retain the failed review and wait for draining. Correlate one container's measured CPU/memory deltas and section timings; do not blindly retry or tune parameters. |
| `inference_stuck` | The inference worker did not exit within the drain timeout. Liveness becomes false. Inspect the backend and Ollama separately before recovery; do not submit overlapping reviews. |
| Port binding failure | Inspect the reported errno/category. Permission restrictions are not proof of occupation; a later successful bind does not establish earlier state. Never kill an unknown listener. |
| Missing local build/acceptance evidence | Inspect Helm release state and rerun the applicable local build or acceptance workflow; do not fabricate success fields. |
| Checkout changed | Helm release state remains in the cluster. Use the same private state root for local evidence. |
| Unknown ownership | Inspect the resource manager and release identity. Automatic takeover is disabled; unknown resources block namespace deletion. |

## Backup and recovery

The application has two PVCs: SQLite history/queue/sessions and DynamoDB Local
accounts. Models live on a separate PVC owned by the independent Ollama release.
Older installations may also retain the retired backend cache. See the
[storage reference](../reference/architecture.md#storage) for paths and sizes.
A consistent SQLite backup requires its backup API or an explicitly
coordinated idle shutdown; copying only the database file during WAL writes is unsafe.
No automatic backup or secure erasure is provided. Any backup may contain user source,
reviews and credentials and needs a private retention policy.

Do not scale the backend above one replica, add workers or change the Recreate strategy.
After process interruption, queued jobs recover and stale running jobs receive at most
the existing retry allowance. Readiness does not prove that historical accounts/results
survived; persistence acceptance is separate.

## Helm and GitOps recovery and cleanup

For local source deployments, use the [local Helm lifecycle](../guides/minikube-demo.md).
For published Helm releases, use [status, uninstall and rollback](../guides/helm-release.md#status-uninstall-and-rollback).
Local CLI evidence is stored under `helm-v1/`; Helm release state is authoritative.
Missing local evidence does not authorize resource adoption or deletion.

```bash
scripts/minikube_demo.sh status --profile minikube
```

```bash
scripts/minikube_demo.sh undeploy --profile minikube
```

The local CLI's normal undeploy retains PVCs and the signing Secret. Its mutation path
checks release ownership, fences admissions and requires an idle backend. Purge needs
explicit `--purge-data --confirm-data-loss NAMESPACE`; external claims and unknown
resources are not silently removed. Inspect failed Helm operations and storage errors
before repeating the same command. Never remove finalizers merely to force success.

For Argo CD, correct or revert the reviewed GitOps source and inspect reconciliation
through the [GitOps guide](../guides/gitops.md). Do not run the Helm CLI against
Argo-owned resources. Keep retained PVCs and external Secrets explicit when planning
application removal; PVC removal does not establish backing-data erasure.

The operator manages cluster shutdown. Preserve failed operation evidence and backups;
do not delete local state, release Secrets or application data to silence errors.

## Success criteria

Record application Ready, quality-valid completed review, persistence checks, complete
API verify and browser acceptance separately. `verify --skip-restart` is incomplete and
nonzero. Follow the [testing evidence rules](../testing/README.md#evidence-and-manual-acceptance);
historical passes do not validate the current deployment.
