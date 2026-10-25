# Consolidating the inference workload

This is a coordinated, maintenance-window migration. These instructions are prepared
for later review; this refactor does not upgrade Helm, sync Argo CD, scale active
workloads, replace secrets, or move model/application data.

## Ownership and data invariants

Keep the existing `local-review` release in `local-review-demo` and the existing
`review-ollama` release in `local-inference` (substitute the actual recorded names
for custom installations). `local-review` continues to own the backend, frontend,
DynamoDB and application PVCs. `review-ollama` continues to own its Deployment,
Service, ConfigMap and `review-ollama-models` PVC. The historical chart/release name
remains to avoid resource adoption or cross-namespace volume copying.

The new inference image runs Ollama and Thrift in one container. The application
chart no longer installs the earlier review-specific `review-inference` worker.
The existing inference Service keeps its DNS name and changes from native HTTP
11434 to Thrift 9091 plus health 11435. Native HTTP binds only to loopback. The GPU
resource `devic.es/dri`, ARM64 selector, krunkit Mesa ICD, non-root UID 10001, pinned
Qwen digest, model mount `/models`, Recreate strategy and retained PVC are preserved.
The backend keeps its current history/DynamoDB claims and `review-secrets` reference.

## Before a future migration

1. Record both current Helm revisions, chart versions, image digests, Argo desired
   revision and release values. Keep an immutable copy of the complete previous
   deployment snapshot; record which manager owns each release.
2. Back up the application SQLite database consistently (include WAL state or use
   SQLite's backup API), DynamoDB Local data, and signing Secret using existing
   private backup procedures. Check model PVC/PV identity and installed manifest
   digest; the model files need no conversion. Do not print secret values in logs.
3. Verify the candidate images and both rendered charts offline. Inspect PVC names,
   claimName fields, annotations, namespaces, selectors and secret references against
   the saved baseline. Do not use Helm `--reuse-values`: obsolete model/HTTP settings
   are rejected by the new schemas. Carry forward the exact storage/secret overrides
   into a fresh values file instead.
4. Choose Helm or Argo CD as the sole deployment manager. Follow the existing
   [ownership procedure](../guides/gitops.md). For Argo, explicitly suspend automated
   synchronization for both applications before manual maintenance; otherwise self-heal
   can undo intermediate steps. Do not mix a Helm upgrade with active Argo ownership.
5. Stop new user traffic, let the durable review queue drain, and stop the backend
   through the selected manager. Confirm that no old review/RPC generation remains
   active before replacing the native workload. An unresponsive connection is not
   evidence that computation stopped. If the previous development chart installed a
   separate `review-inference`, drain and remove it through that same release.

## Upgrade order

1. Upgrade the existing `review-ollama` release, preserving its namespace and model
   claim. The same Deployment is recreated with the independent inference image.
   It starts Ollama, checks the model pin, runs a generic GPU warmup, then opens RPC.
2. Run its Helm test or inspect the Argo PostSync verification Job. That probe uses
   generated Thrift calls; it needs neither a GPU allocation nor application secrets.
3. Upgrade `local-review` with the backend image and new connection settings. Default
   `inference.host` is `review-ollama.local-inference.svc.cluster.local`, port 9091.
   Configure application `model.expectedId/expectedRevision` only if enforcing a
   selected model; the service's execution settings live in its own chart.
4. Check the backend handshake/readiness, register/login with an existing account,
   verify old history and perform one new real review. Confirm model provenance,
   queue recovery, HTTP errors and metrics. Only then restore traffic and automatic
   synchronization under the selected manager.

There are still two Deployments and corresponding Services on the communication
path: FastAPI and combined inference. Frontend and DynamoDB remain supporting
infrastructure. With an enforcing CNI, backend egress selects the preserved inference
release on 9091; the inference policy selects the backend namespace/release and local
verification pods. There is no cluster route to native 11434. The local application
profile disables its own policy; adjust the inference policy explicitly if custom
namespaces/releases are used. Kubernetes probes use health 11435.

CI's historical release-image key/repository suffix `ollama` now identifies the
combined inference image. Its fingerprint includes `inference-service`, Vulkan image
inputs and the shared contract; backend fingerprints include the contract too. This
keeps existing release provenance and rollback tooling readable without migrating
release ownership. A backend-only code change does not rebuild/restart inference.

## Rollback

Stop admission and drain/stop the new backend first. Restore **both** prior chart
snapshots, values and image digests through their original manager, inference first,
then backend. Restoring only an image is insufficient because the Service ports,
RPC protocol, ConfigMaps, policies and probes changed. With Helm, use the two recorded
revision numbers; with Argo, restore the previous immutable deployment snapshot for
both applications and review the diff before resuming automated sync. Re-run the
previous release's verification procedure and a real application review.

Keep the same PVCs and signing Secret throughout rollback. The refactor introduces
no database schema or model-file migration. Never uninstall releases, delete PVCs,
recreate signing material, change namespaces or allow Argo to adopt another manager's
resources as a shortcut. In-flight service results are memory-only and do not survive
replacement. Durable backend jobs follow the existing bounded interrupted-job retry
policy, which may repeat computation after a crash; audit history before replaying
an operation whose completion is unknown.
