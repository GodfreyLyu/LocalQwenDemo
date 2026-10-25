# Operations, recovery, and cleanup

[Documentation index](../README.md)

This runbook covers application startup and recovery for Helm and Argo CD deployments.
For Helm lifecycle operations, use the [Helm guide](../guides/helm-release.md); for
Argo CD, use the [GitOps guide](../guides/gitops.md).
Start with read-only diagnostics on an explicitly selected profile. Inspection does not
authorize new inference, restarts, configuration changes or deletion. Preserve data and
reports, including failed and incomplete results.

## Local endpoint access

If a local URL is unavailable, check the background endpoint launcher from the
main source checkout:

```bash
python3 scripts/minikube_endpoints.py status
```

Repeat any custom `--context` and `--state-dir` options used at startup. `READY`
confirms a forwarding listener, not application health. A skipped service needs
to be installed and have ready Pods before a fresh start can select it. The
launcher retries interrupted forwards that it has already started. See the
[endpoint guide](../../scripts/README.md#background-minikube-endpoints) for start
and stop commands, logs and port conflicts, then inspect the workloads below.

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
accounts and the Thrift worker handshake have passed. The local Helm CLI deployment timeout defaults to
3900 seconds and its warm verification budget to 600 seconds. The Helm profile allows
300 seconds for a whole review; direct local development defaults to 180 seconds.
A longer startup allowance does not extend the inference timeout.

### Thrift worker and Ollama checks

The backend is one workload. The generic inference image owns both the Thrift
server and its Ollama child in the preserved `review-ollama` release. Native HTTP
is loopback-only; do not debug by adding a Service port or changing it to wildcard.
Inspect fixed service startup codes and the original PVC/model pin first:

```bash
kubectl --context minikube -n local-inference logs deployment/review-ollama -c inference --timestamps --tail=100
```

```bash
kubectl --context minikube -n local-inference exec deployment/review-ollama -c inference -- ollama ps
```

```bash
kubectl --context minikube -n local-inference port-forward service/review-ollama 19091:9091 --address 127.0.0.1
```

Use a generated client through that forward. A listening socket alone does not
establish model readiness; call `getHealth`/`getModelInfo` and use the generic probe
or a real application review. A lost start response is reconciled by request ID,
never blindly resubmitted. Unknown/expired IDs and changed service instances cannot
establish what the old operation did. See the [Thrift guide](../guides/apache-thrift.md).

Active cancellation stays `CANCELLING` until the native process group is reaped.
The service then reloads the pinned model; admission/readiness remain closed during
recovery. If exit cannot be established, it publishes no terminal cancellation and
fails closed. Unexpected child exit ends the service so the workload can restart.
Results are memory-only and expire after the advertised retention period.

`inference_stuck` closes backend admission and liveness. Inspect the inference
instance, pending/active state and durable queue before restarting or replaying
work. A backend restart may requeue an interrupted review and repeat computation.
The [migration and rollback procedure](inference-migration.md) preserves both
release owners, all PVCs and signing material. Do not alter active ownership to
work around an uncertain result.

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
| Model download or worker startup failure | Inspect the Thrift worker and Ollama separately. Check version, digest, capability and context; only Ollama downloads model files. Preserve its model PVC and partial downloads. |
| `ollama_model_mismatch` during backend startup | Compare the backend's optional model identity assertions with the service's actual model/revision. Per-request options must fit published limits; complete service configuration is not mirrored in the backend. Restart to apply environment changes. |
| Account dependency unavailable | Confirm the local endpoint, dummy credentials, disabled SDK metadata/shared configuration and ACTIVE table/schema. The initializer is idempotent and refuses remote endpoints or schema replacement. |
| 415, 403 or 401 | POST/PUT/PATCH require JSON; Origin must match the configured scheme, host and port, and authenticated writes require the session's CSRF token. Use the documented localhost URL and sign in normally. |
| `inference_timeout` | Retain the failed review and wait for draining. Correlate one container's measured CPU/memory deltas and section timings; do not blindly retry or tune parameters. |
| `inference_stuck` | A drain expired or RPC execution became uncertain. Liveness becomes false. Inspect backend, Thrift worker and Ollama; a socket failure does not prove that generation stopped. |
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

Keep both backend and combined inference at one replica and preserve their `Recreate`
strategy. Adding Uvicorn workers or inference replicas is not a supported recovery step.
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
