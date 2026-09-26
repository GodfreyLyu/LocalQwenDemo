# Operations, recovery, and cleanup

[Documentation index](../README.md)

This runbook covers the local CPU service deployed to an existing minikube cluster.
Start with read-only diagnostics on an explicitly selected profile. Inspection does not
authorize new inference, restarts, configuration changes or deletion. Preserve data and
reports, including failed and incomplete results.

## Health and startup

```bash
scripts/minikube_demo.sh status --profile minikube
scripts/minikube_demo.sh logs --profile minikube
scripts/minikube_demo.sh doctor --profile minikube
```

These commands check ownership and use the selected target's private kubeconfig
with an explicit context and namespace. Do not print kubeconfig, acceptance accounts,
Secret contents, complete environment dumps, user history bodies or raw exception strings.

`/health/live` is available during loading. `/health/ready` remains 503 until storage,
accounts and the startup-validated model are ready. The default cold deployment budget
is 3600 seconds and warm budget 600 seconds; the whole-review inference timeout remains
300 seconds. A longer startup allowance does not change inference parameters.

| Symptom | Evidence and safe next step |
| --- | --- |
| No running profile or API unavailable | Start/fix the cluster yourself, then rerun diagnostics. The script never starts or recreates a cluster. |
| Pending backend, resource warnings | Inspect actual allocatable, requests/limits and usage, host swap, Docker capacity and disk. Do not treat unavailable metrics as sufficient capacity. |
| `Unable to access jarfile DynamoDBLocal.jar` | Verify the pinned image and effective identity. The minikube configuration uses image UID 1000, group/fsGroup 10001 and a root-only volume-permission initializer limited to the PVC root. Do not recursively change data permissions or replace the PVC. |
| Model download finishes but startup fails | Check safe startup stage/error metrics and every required indexed shard, including broken links/readability/structure. A 100% progress indicator is not a complete cache. Preserve valid blobs, partial downloads and the cache PVC. |
| Download service error | Preserve the pinned revision and cache; the existing HTTP downloader disables Xet. A separately authorized repair may resume necessary files, never replace incomplete files with unverified weights. |
| Account dependency unavailable | Confirm explicit local endpoint, inert credentials, disabled SDK metadata/shared configuration and ACTIVE table/schema. The initializer is idempotent and refuses remote endpoints or schema replacement. |
| 415, 403 or 401 | POST/PUT/PATCH require JSON; Origin must exactly match localhost and authenticated writes require the session's CSRF token. Sign in normally; never bypass media-type/auth/CSRF checks. |
| `inference_timeout` | Retain the failed review and wait for draining. Correlate one container's measured CPU/memory deltas and section timings; do not blindly retry or tune parameters. |
| `inference_stuck` | The native generation did not drain within the existing bound. Liveness becomes false. Inspect before any separately authorized workload recovery; never start a second model process. |
| Port binding failure | Inspect the reported errno/category. Permission restrictions are not proof of occupation; a later successful bind does not establish earlier state. Never kill an unknown listener. |
| Missing/incomplete deployment record or changed fingerprint | Finish a normal owned up; do not fabricate expected images, fingerprints, ports or success fields. |
| Checkout changed | Reuse shared state, or import a trusted old state copy. The path itself does not confer ownership. |
| Unknown ownership | Refuse adoption. If all state is lost and all data may be abandoned, use the separate recovery preview; unknown resources still block deletion. |

## Backup and recovery

Three PVCs separately hold SQLite history/queue/sessions, model cache, and DynamoDB
Local accounts. A consistent SQLite backup requires its backup API or an explicitly
coordinated idle shutdown; copying only the database file during WAL writes is unsafe.
No automatic backup or secure erasure is provided. Any backup may contain user source,
reviews and credentials and needs a private retention policy.

Do not scale the backend above one replica, add workers or change the Recreate strategy.
After process interruption, queued jobs recover and stale running jobs receive at most
the existing retry allowance. Readiness does not prove that historical accounts/results
survived; persistence acceptance is separate.

## Shared state and cleanup

Use the maintained [state import and normal undeploy procedure](../guides/minikube-demo.md#undeploy-and-recovery).

```bash
scripts/minikube_demo.sh import-state --profile minikube --from-state /path/to/old/checkout
scripts/minikube_demo.sh undeploy --profile minikube
```

Import is appropriate only when a trusted old copy exists. Default undeploy removes
owned runtime resources but preserves the three PVCs, signing Secret and recovery
information. It checks readiness/queue state, pauses the owned frontend, reserves SQLite
writes to fence racing requests, and conditionally deletes verified resources. A queued,
running, draining or unmeasurable inference prevents cleanup.

For deliberate full data removal under trusted state:

```bash
scripts/minikube_demo.sh undeploy --profile minikube \
  --purge-data --confirm-data-loss local-review-demo
```

Normal purge retains the namespace. With lost state, use the separate
[lost-state recovery procedure](minikube-lost-state-recovery.md): explicit target identities,
a read-only preview, complete resource enumeration and separate deletion confirmation.
It never manufactures owner state or adopts unknown resources. Partial failure retains
recovery evidence; no finalizer is removed and no unfinished cleanup is called successful.
PVC removal does not prove backing data erasure.

The script cannot intercept manual minikube shutdown. If cleanup is wanted before stopping
the cluster, successfully complete the chosen cleanup first, then manage the cluster
yourself, considering other projects. Do not delete state files or data to silence errors.

## Success criteria

Record application Ready, quality-valid completed review, persistence checks, complete
API verify and browser acceptance separately. `verify --skip-restart` is incomplete and
nonzero. Follow the [testing evidence rules](../testing/README.md#evidence-and-manual-acceptance);
historical passes do not validate the current deployment.
