# Minikube incident findings — 2026-09-20 to 2026-09-21

These findings describe the historical Docker-driver Minikube deployment. They do
not validate the current cluster. Use the [current deployment guide](../guides/minikube-demo.md)
and [recovery runbook](../operations/recovery-and-cleanup.md) for operating instructions.
Detailed repair journals and general test logs remain available in Git history.

## Resource diagnostics blocked deployment (2026-09-20)

`doctor`, automatic-target `up` and `up --profile minikube` found a running cluster
with an available API, then exited with code 1. The node advertised 10 CPUs and
15.6 GiB allocatable memory, but its Docker limits were only 2 CPUs and 3.91 GiB.
The application needed about 2.145 CPUs and 4.34 GiB in requests, with 6.84 GiB in
memory limits when init containers were conservatively included. Host memory
pressure and a two-minor-version gap between kubectl 1.36.4 and Kubernetes 1.34.0
were additional blockers. Disk space was sufficient; Pod metrics were unavailable.

No images were built or loaded, no application resources were created, and no
model download, review or browser acceptance ran. Existing cluster configuration,
deployment state and global kubeconfig were unchanged. Resource diagnostics became
advisory for `up` on 2026-09-21; that behavior change was verified offline only.

## DynamoDB Local image identity and startup permissions (2026-09-21)

The inspected ARM64 `amazon/dynamodb-local:3.1.0` image used UID/GID `1000:1000`.
Its `/home/dynamodblocal` directory had mode `0700`, so the failing Pod's UID 10001
could not reach `DynamoDBLocal.jar`. Changing the JAR path would not fix the directory
permissions. The recorded image contract is retained in the
[image fixture](../../scripts/tests/fixtures/dynamodb-local-3.1.0-arm64.json).

The repair changed the DynamoDB process UID and volume-root owner to 1000 while
retaining GID/fsGroup 10001. Only the dedicated volume root was set to
`1000:10001` / `0770`; existing database files were not recursively changed.
The inspected data directory was empty. The container retained its non-root,
read-only filesystem, capability and privilege-escalation restrictions.

The replacement Pod stayed Ready with zero restarts across six observations over
75 seconds. Its actual UID/GID was `1000:10001`, effective capabilities were zero,
and `NoNewPrivs=1`. Two initializer runs succeeded: the new `llm-review-users`
table was ACTIVE with a string `login_id` HASH key, and the second run preserved
its identity and schema. The account count remained zero.

All three PVCs, the signing Secret and other application Deployments were unchanged.
The earlier failed deployment report remained failed. This ARM64 repair did not
establish AMD64 behavior, full application acceptance, a real review or browser acceptance.

## OpenMP setting and measured evidence (2026-09-21)

The overlay retained the operator's `OMP_NUM_THREADS=2` setting after the
[CPU A measurement](minikube-cpu-measurement-2026-09-21.md) and
[CPU B measurement](minikube-cpu-b-measurement-2026-09-21.md). A timed out; B completed
one review under the unchanged model, quality rules, token budget and deadline.
B also ran with less host swap pressure. The comparison does not isolate the OpenMP
effect, establish repeatability or prove hardware-accelerated BF16 execution.
The original reports and JSON evidence retain the timings and resource measurements.

## Model-cache repair verification (2026-09-21)

Startup failed because the pinned model snapshot lacked its first shard. That shard
existed only as a 2,443,182,080-byte `.incomplete` download; its required size was
3,441,185,608 bytes. The second shard was present at 622,329,984 bytes. Available
disk space and readable files did not explain why the download had stopped. Zero
observed backend OOM counters did not rule out earlier node pressure.

The official downloader resumed the missing shard in the same PVC. Both completed
files matched the pinned revision's sizes and hashes:

| Shard | Bytes | SHA-256 |
| --- | ---: | --- |
| `model-00001-of-00002.safetensors` | 3441185608 | `169ad53ec313c3a34b06c0809216e4fc072cce444a5d4ff2b59690d064130ed5` |
| `model-00002-of-00002.safetensors` | 622329984 | `912becff8d60672aa8628ef08c05898d9adf17c2ad4ae3caf99b065622fdeff9` |

The index matched Git blob `986d7db875b47d21f68530f6baac038f1b297b39`, and every
indexed tensor was checked through safetensors metadata. The existing second shard
retained its inode and modification time. No PVC was recreated, and no second model
was loaded during repair. The temporary non-root Pod was removed afterward.

The first backend image update passed startup but was rolled back because partial
deployment state lacked image maps. The final update preserved target identity and
changed only backend image records, without rewriting earlier acceptance results.
Its Pod emitted `model_ready`, returned HTTP 200 from readiness and stayed Ready with
zero restarts across three observations over 60.8 seconds. PVC identities and the
DynamoDB/frontend Deployment resource versions were unchanged.

This established backend startup only. No completed review or browser acceptance was
run, and the memory observations did not justify reducing the 6 GiB backend limit.

## Port-forward incident (date not separately recorded)

The supplied incident report recorded review
`2103c951-8f0d-4868-827e-cc258438d2e4` as completed, followed by failure at
`persistence_acceptance`. Both `persistence_verified` and `api_acceptance_passed`
remained false. The follow-up neither rewrote that result nor submitted another review.

An offline macOS experiment reproduced `EADDRINUSE` (errno 48) after server-initiated
connection closure. Reusable bind/listen succeeded unless an active listener held
the port. This supported changing listener probes, but did not establish TIME_WAIT
as the cause of the original incident. Persistence after restart, complete verification
and browser acceptance still required separate runtime checks.
