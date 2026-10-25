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

The base application Chart and the smaller backend profile have different resource
settings. The maintained Argo CD application uses the smaller profile from
`values-minikube-ollama.yaml`, since inference runs in a separate Ollama Pod.

| Workload / profile | CPU request / limit | Memory request / limit |
| --- | --- | --- |
| Backend, base Chart | 2 / 2 CPUs | 4 / 6 GiB |
| Backend, `values-minikube-ollama.yaml` | 100m / 1 CPU | 256 / 768 MiB |
| Frontend | 25m / 200m | 32 / 96 MiB |
| DynamoDB Local | 100m / 500m | 256 / 512 MiB |
| Combined inference + Ollama, default Chart | 250m / 2 CPUs | 1 / 3 GiB |

Monitoring, Kubernetes components and other workloads need additional capacity.
The independently packaged inference image owns RPC, native process state, model
weights and GPU use in one container. See its
[Chart guide](../../deploy/helm/local-ollama/README.md) for the preserved GPU setup.

The application Chart requests two PVCs: 10 GiB for history/queue/sessions and
1 GiB for accounts. Ollama adds a 4 GiB model PVC; optional Prometheus monitoring
adds a 5 GiB PVC. These requests do not
reserve physical host disk, and declared sizes are not measured usage.
Older installations may still retain an unused backend model-cache PVC; see the
[storage reference](architecture.md#storage) for upgrade retention.

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

The [historical CPU reports](../reports/README.md) describe the removed Transformers
implementation. Their weight sizes and A/B timings do not measure current Ollama
performance. Host load differed between those runs, so they also do not isolate the
cause of the timing difference. Measure the current Ollama container and record its
model, hardware and workload when comparing performance; see the
[measurement guidance](observability.md#inference-and-startup-measurement-boundaries).

There is one backend replica and one combined inference replica. Both use `Recreate` rollouts, so updates and recovery
interrupt availability. There is no horizontal scaling, distributed queue or high
availability. The coordinator closes admission if a drain exceeds its allowance
or remote execution becomes uncertain. Cancellation reaps the native process group and reloads the pinned model; see the
[Thrift guide](../guides/apache-thrift.md) for the execution guarantees.

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
