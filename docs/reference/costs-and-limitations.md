# Local resources and limitations

[Documentation index](../README.md)

The service uses your host, Docker VM and existing minikube capacity. There is no cloud
provisioning or remote image publication workflow. Local disk, RAM, CPU, power and first
download bandwidth are still real costs; no command assumes that shared cluster capacity
is free merely because the cluster already exists.

## Resource diagnostics

The backend requests 2 CPUs and 4 GiB memory, with limits of 2 CPUs and 6 GiB. DynamoDB
Local, frontend, Kubernetes components and other workloads consume additional capacity.
Three PVCs request 12 GiB for model cache, 10 GiB for history/queue/sessions and 1 GiB
for accounts. These are allocation requests, not proof of actual host free space or
securely reserved physical disk. Pinned weights total approximately 4.08 GB.

`doctor` separately measures host memory pressure, Docker capacity, node allocatable
resources, existing requests/limits and available usage metrics, and host/VM disk budgets.
Repeated deployments avoid double-counting owned allocations and reusable images. Missing
measurements remain `not_measured`, not zero usage or a passing check. It exits nonzero
for failed/incomplete diagnostics. `up` reports resource/version warnings and attempts
deployment, while target, identity, architecture and storage requirements remain mandatory.
Only the user can resize or start the cluster; the scripts cannot manage its lifecycle.

## Performance and availability

Real BF16 CPU inference is slow and host-dependent. The fixed model, 2 model threads,
`OMP_NUM_THREADS=2`, 384-token total budget and 300-second whole-review timeout are not
adapted automatically to make acceptance pass. The historical successful B measurement
had different host swap pressure from A; neither proves a hardware BF16 execution path,
repeatability, causal isolation or the performance of this checkout.

There is one backend replica and one inference worker. Rollout/recovery can interrupt
availability; there is no horizontal scaling, distributed queue or high availability.
Timeouts drain cooperatively before another review may start. A native thread that will
not drain causes `inference_stuck` and failed liveness; do not overlap another model process.

## Storage and network limits

Local hostpath storage is not a backup or encryption guarantee. Default undeploy preserves
PVCs and signing material. Confirmed data purge may leave backing data when the PV reclaim
policy is Retain; even Delete does not prove erasure. No automatic backup, secure wiping
or account/history expiry is implemented. Shared state contains private kubeconfig and
acceptance credentials; retain it securely across checkout moves.

NetworkPolicy effectiveness depends on the existing CNI. The script never installs or
reconfigures CNI/storage to satisfy a check. Unsupported storage or cross-architecture
execution blocks deployment. Loopback entry is intended for the local operator; the
project is not validated for public or hostile multi-tenant exposure.
