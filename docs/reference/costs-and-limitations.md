# Local resources and limitations

[Documentation index](../README.md)

The application and its default Ollama service use local Minikube capacity. The
krunkit GPU setup and Docker-driver development workflow have different host and VM
requirements. Both consume local disk, RAM, compute, power and download bandwidth.
Allow capacity for other workloads sharing the cluster.

GitHub Actions builds release images and publishes them to GHCR. The operator provides
the cluster; the release workflow does not provision cloud infrastructure. See the
[GitOps guide](../guides/gitops.md) for image publication and deployment ownership.

## Resource diagnostics

By default, the backend requests 2 CPUs and 4 GiB memory, with limits of 2 CPUs and 6 GiB. DynamoDB
Local, frontend, Kubernetes components and other workloads consume additional capacity.
The application Chart requests three PVCs: 12 GiB for the backend model cache, 10 GiB
for history/queue/sessions and 1 GiB for accounts. The independent Ollama Chart adds a
4 GiB model PVC and its own CPU, memory and GPU requests. These storage requests do not
reserve physical host disk. The optional `values-minikube-ollama.yaml` overlay reduces
backend resource requests because inference runs in Ollama. See the
[Ollama Chart](../../deploy/helm/local-ollama/README.md) for its requirements.

The removed Transformers CPU path used approximately 4.08 GB of pinned BF16 weights.
That figure is not the size of Ollama's quantized model.

For the Docker-driver workflow, `doctor` separately measures host memory pressure,
Docker capacity, node allocatable resources, existing requests/limits and available
usage metrics, and host/VM disk budgets.
Repeated deployments avoid double-counting owned allocations and reusable images. Missing
measurements remain `not_measured`, not zero usage or a passing check. It exits nonzero
for failed/incomplete diagnostics. `up` reports resource/version warnings and attempts
deployment, while target, identity, architecture and storage requirements remain mandatory.
The application deployment CLI does not resize or start clusters. Cluster lifecycle
is separate; the krunkit platform provides its own startup helper.

## Performance and availability

The following historical measurements describe the Transformers CPU path; they are
not Ollama GPU benchmarks. Those runs used a fixed model, BF16 CPU inference, two
model threads, a 384-token total budget, and a 300-second whole-review timeout.
The B run added `OMP_NUM_THREADS=2`; the other inference settings stayed fixed.
The successful historical B measurement
ran under different host swap pressure from A. Neither run proves hardware-accelerated
BF16 execution or repeatable performance. The comparison does not isolate the cause of
the improvement or measure this checkout's performance.

There is one backend replica and one inference worker. Rollout/recovery can interrupt
availability; there is no horizontal scaling, distributed queue or high availability.
The coordinator retains its drain/stuck protection for unresponsive worker calls.
Ollama cancellation closes the local HTTP operation; the Ollama service owns remote
generation cleanup. See the [model reference](model.md) for adapter behavior.

## Storage and network limits

Local hostpath storage is not a backup or encryption guarantee. Default undeploy
preserves PVCs and signing material. Even a confirmed data purge may leave the
underlying data when the PV reclaim policy is `Retain`. A `Delete` policy does not prove
that the data was erased either. No automatic backup, secure wiping or account/history
expiry is implemented. Shared state contains private kubeconfig and acceptance
credentials; retain it securely across checkout moves.

NetworkPolicy effectiveness depends on the existing CNI. The script never installs or
reconfigures CNI/storage to satisfy a check. Unsupported storage or cross-architecture
execution blocks deployment. Loopback entry is intended for the local operator; the
project is not validated for public or hostile multi-tenant exposure.
