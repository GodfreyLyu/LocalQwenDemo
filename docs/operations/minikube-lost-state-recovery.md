# Minikube cleanup after ownership state is lost

Use this destructive recovery path only when the old deployment state is unavailable and
**all application data may be discarded**. If a trusted state copy exists, prefer
[import-state and normal undeploy](../guides/minikube-demo.md#undeploy-and-recovery).
Normal `undeploy` requires trusted ownership records, preserves data by default, and
retains the namespace even with its confirmed purge option. Recovery never adopts
resources or manufactures an `owner.json`: it independently validates the actual target
and can delete the namespace only when every listed resource is explainable.

The cluster must already be running. You manage its lifecycle. This command never starts,
stops, recreates or deletes minikube, modifies Docker/CNI/StorageClass, accesses AWS, runs
inference or operates on another project's resources. Existing forwarding processes are
not killed or reused. No operational command below was run as part of the offline
implementation tests.

## 1. Read and independently confirm identities

Run from the checkout root. Select the actual running profile explicitly; the name below
is an example, not an inferred target. Use the same `--minikube-home` and `--state-root`
overrides on every command if your installation uses them.

```bash
PROFILE=minikube
scripts/minikube_demo.sh inspect-target --profile "$PROFILE"
```

`inspect-target` uses the existing target selection checks and a temporary private
kubeconfig. It prints only profile/home, cluster UID, namespace UID and the namespace's
ownership marker. It does not change the global current-context, import old state, or
write deployment/acceptance records. Public identities are confirmation inputs, not
proof that all namespace contents belong to this application.

Compare the identities with the deployment you intend to discard. An absent marker or
namespace cannot be repaired by guessing a value, adding labels, or deleting state.
Substitute the observed and independently confirmed values; no real target IDs are
embedded here:

```bash
CLUSTER_UID='<confirmed-cluster-uid>'
NAMESPACE_UID='<confirmed-namespace-uid>'
OWNER='<confirmed-original-48-character-owner-marker>'
TARGET_ARGS=(--profile "$PROFILE" --expect-cluster-uid "$CLUSTER_UID" \
  --expect-namespace-uid "$NAMESPACE_UID" --expect-owner "$OWNER")
scripts/minikube_demo.sh recover-cleanup "${TARGET_ARGS[@]}"
```

This is a **read-only preview**, not deletion authorization. Apart from the shared local
operation lock and temporary private kubeconfig, it creates no state or resources. It
lists verified resources, recognized system resources, unknown identities, and PVC/PV
reclaim policies. Queue eligibility is reported as `not_measured` in the preview; a
preview is not evidence that inference is idle. Execution performs the live checks and
writer reservation described below.

Every listable namespaced API resource type must be enumerated successfully. Discovery
failures, missing permissions, duplicate/ambiguous identities, or unknown objects stop
cleanup rather than being interpreted as an empty namespace. Directly managed resource
types require the exact original marker. Unmarked ReplicaSets, Pods and EndpointSlices
must have a single controller ownerReference with the actual parent's UID, kind, API
version and name, traced to a verified root. A similar name, label or marker on a Pod
alone is insufficient. Resources with unsupported or missing ownership links (including
legacy Endpoints or Events without a verifiable ownerReference) are conservatively
reported as unknown. Inspect them; do not relabel them to force authorization.

The default ServiceAccount and `kube-root-ca.crt` ConfigMap are not accepted by name alone.
They require recorded Kubernetes-controller provenance, no foreign owner/marker, and
additional checks: the ServiceAccount has no custom token/pull-secret configuration,
and the ConfigMap contains only the local minikube CA certificate. Missing provenance
or unexpected content blocks cleanup. Other resources are never classified as system
resources merely because their names look familiar.

## 2. Explicitly abandon data and execute

Only proceed when every listed object is understood, all application data may be lost,
and nobody else is changing this namespace. Execution requires **all three** flags:

```bash
scripts/minikube_demo.sh recover-cleanup "${TARGET_ARGS[@]}" \
  --execute --purge-data --confirm-data-loss local-review-demo
```

This deletes accounts, sessions/history/queue data, the signing Secret, model cache,
the three PVCs, application runtime resources, and the verified namespace. It preserves
existing local owner/build/deployment/acceptance records and credential files as private
historical evidence; it does not claim that old builds or reviews validate new code.
Trusted complete ownership state redirects you to normal `undeploy`; conflicting or
unreadable state stops recovery without overwriting it.

The shared target lock excludes this project's `up`, `undeploy`, imports, and recovery
commands from every checkout. Before stopping anything, the existing backend must be
ready with no queued/running/draining inference. Recovery pauses the verified frontend
with UID/resourceVersion patch tests, waits for its Pods to exit and rechecks the queue.
It then reuses the existing SQLite writer reservation in the running backend: new review
commits, including direct backend submissions, cannot enter while the backend is being
stopped. No additional model process is launched. Busy, draining, expired or unavailable
guards fail closed. An externally restarted workload invalidates the stopped proof.
Deployment generation is checked as well as UID and replicas, so a scale-up followed
by scale-down cannot reuse an earlier idle proof merely because replicas are zero again.

The entire namespace inventory is revalidated before workload changes, before each
managed-resource deletion and immediately before namespace deletion. Every deletion
uses API UID/resourceVersion preconditions and bounded waits (`--delete-timeout`, default
120 seconds per resource, accepted range 1–600). No `delete all`, label-only bulk deletion,
or finalizer removal is used. Controller deletion precedes storage deletion.

These checks and deletions are **not a multi-object atomic transaction**. The lock cannot
stop manual kubectl operations, external controllers, or other administrators. Stop other
writers and restrict namespace access during recovery. Preconditions protect the object
being deleted; a namespace resourceVersion does not lock the namespace's children.
Re-enumeration narrows the remaining window but cannot eliminate it.

### PVC removal is not secure erasure

The preview and journal record each bound PV UID, verified claimRef, and actual reclaim
policy. Pending/unbound claims are identified separately; unknown binding/policy blocks
cleanup. `Retain` can leave the volume and its data behind. `Delete` asks the storage
provisioner to reclaim storage, but namespace/PVC disappearance does not prove that the
underlying files were erased. `underlying_data_erasure` remains `not_verified`.
The recovery command never deletes PV objects directly or touches host directories.
Any further storage inspection/erasure requires separate ownership evidence and authorization.

## 3. Failure, interruption and retry

The private target directory contains an independent `recovery-cleanup.json` (directory
mode 0700, file mode 0600). It records verified identities/owner chains, phase, frontend
pause state, individual deletion results, remaining resources, and `in_progress`,
`failed`, `interrupted` or `cleaned`. It contains no Secret data, account credentials,
history bodies, prompt or generated text. `remaining_status=not_measured` means a failure
prevented a fresh inventory; a retained list must not be treated as a complete current
inventory. The command does not modify normal deployment/verification records during
recovery. Those older records are historical evidence, not current application readiness.

After resolving the reported permissions, resource, or finalizer issue, repeat the same
preview and explicitly confirmed execution command. Already absent objects are reconciled;
a same-name replacement UID is rejected. A timeout or finalizer block is not success.
If the namespace disappeared after this operation recorded its deletion intent, a repeat
can confirm actual absence and complete the recovery journal. Without that evidence, an
absent namespace is not reported as this command's cleanup success.

A failure during the initial queue-fencing phase attempts to restore the original
frontend replica count after verifying its UID. Later deletion failures may leave the
frontend paused or already deleted. Inspect `frontend.status` in the journal. To restore
**only an existing frontend paused by this recovery**, use:

```bash
scripts/minikube_demo.sh recover-cleanup "${TARGET_ARGS[@]}" --restore-frontend
```

This separate action verifies namespace/owner/frontend identity and patches replicas
with UID/resourceVersion tests. It does not recreate a deleted Deployment, restart the
backend, establish readiness, or mark cleanup successful. A replaced frontend is never
modified. Do not combine restoration with deletion flags.

A crash after backend shutdown but before the completed queue-fence checkpoint is
ambiguous. With retained history and no confirmed idle proof, retry stops. Do not edit
`quiesced`, fabricate ownership, force deletion, or assume missing Pods imply an empty
queue. A separately reviewed, identity-protected backend restoration and fresh ready/
queue check is needed before cleanup can proceed. This conservative case is intentionally
not repaired by automatically launching a model or discarding potentially queued work.

## 4. Start a new deployment only after confirmed cleanup

Only after `cleaned` and observed namespace absence:

```bash
scripts/minikube_demo.sh up --profile "$PROFILE"
```

The explicit `up` rechecks namespace absence and matching recovery identity, archives the
old partial records and completed recovery journal, and creates normal new ownership for
new resources. Interruption during archival retains recoverable copies. An unexpected
replacement namespace blocks this transition. It builds/verifies actual checkout inputs
with the normal unique image tags, image IDs and fingerprints. It never reuses an old
review as evidence for new code. Nothing here runs `up` automatically or establishes
completed-review, persistence, complete `verify`, or browser acceptance.
