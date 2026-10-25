# Generic LLM service verification — 2026-10-25

This verifies the uncommitted refactor on `feat/switch_to_thrift`. No active Helm or
Argo CD release was upgraded, synchronized or scaled. No commit or push was made.
The initial changed files/diff were saved privately in `.local/generic-llm-initial.zip`
before refactoring. Authentication/persistence implementation, application PVC
resources, the model PVC template and krunkit Mesa repository file retain their
existing content; inference references and container selectors changed where needed.

## Checks completed

| Check | Result and coverage |
| --- | --- |
| Backend offline suite | **214 passed**, 2 opt-in real tests deselected. Authentication, sessions, CSRF, queue admission/recovery, SQLite persistence, history, review validation, public error codes, shutdown and uncertain RPC state. |
| Independent inference gate | **43 passed**. Real generated Python/Java clients and processors over TCP; generic translation requests; shared IDs across languages; concurrency/capacity, TTL retention, conflicts, queued/active deadlines, cancellation, lost TCP responses, real service-process replacement, child startup/crash/group cleanup and shutdown. Native HTTP and generated content are controlled doubles in this gate. |
| Tooling suite | **242 passed**, using installed Prometheus tooling. Helm schemas/invariants, release fingerprints, shared contract rebuilds, GitOps/rollback provenance, deployment utilities and evaluator contracts. The evaluator's 36 tests were also rerun after its final dependency cleanup. |
| Frontend | Lint/format, **30 unit/component tests**, production build, and **6 Chromium browser tests** passed. Browser tests use emulated accounts and model fixtures. |
| Bindings | Apache Thrift **0.25.0** regenerated Python and Java output; byte-for-byte drift check passed. The small Java example compiled and ran with SHA-256-pinned jars. |
| Kubernetes | Both charts linted. **27 rendered resources valid**, zero invalid/errors/skipped, using strict Kubernetes **1.35.0** schemas plus repository monitoring CRD schemas. Includes inference NetworkPolicy, Service/Deployment/probes and verification hooks. |
| Monitoring | **9 alert rules** and their scenarios, plus **37 dashboard expressions**, validated with Prometheus **3.15.0** promtool. Container selectors now target the combined `inference` container. |
| Independent images | Repository-root builds of `review-backend:generic-llm-check` and ARM64 `review-inference:generic-llm-check` passed. Network-isolated container checks verified independent imports, real TCP generation with a model double, and actual child-process cleanup. The inference image ran Python **3.15.1**; backend uses **3.12**. |
| Privacy / worktree | Error/log sentinel checks passed. Changed Markdown local links and `git diff --check` passed. No changes were staged. |

The image's installed pins were queried directly:

- `ollama-0.24.0-4.fc45.aarch64`
- `ollama-vulkan-0.24.0-4.fc45.aarch64`
- `mesa-vulkan-drivers-25.3.6-102.fc44.aarch64`
- `mesa-filesystem-25.3.6-102.fc44.aarch64`

## Real model execution

A temporary service used the real, unmodified `llm_service.runtime` entry point and
supervised its own host Ollama **0.35.1** child. It reused the installed model store
without downloading or replacing model files. A separate native loopback port
**19434**, RPC **19091** and health **19435** avoided the Mac's existing Ollama daemon.
The temporary service exited successfully and cleaned up its owned child.

Actual model: `qwen3:1.7b`, revision
`sha256:8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7`.
Native resident metadata reported complete GPU placement on the Mac (Metal), which
is different from the Linux Vulkan deployment target.

Observed real checks:

1. A generic Python translation generation completed: 21 input tokens, 3 output
   tokens, finish reason `stop`.
2. The compiled Java client retrieved the **same retained request ID and result**
   through TCP Thrift, with matching revision and usage.
3. An active real generation was cancelled. The service reaped/restarted its native
   child, returned terminal cancellation and recovered readiness.
4. Both opt-in backend real-model tests passed: a full three-section review, and
   authenticated FastAPI submission with persisted remote model identity.

Raw operational artifacts remain ignored under `.local/real-llm`; service logs
contain fixed events/codes rather than prompts, submitted source or generated text.
The sample clients intentionally display their synthetic translation result.

## Verification limits

- The new image was **not deployed to the active krunkit cluster**. Its Vulkan GPU
  execution and live CNI enforcement were not rerun. The pinned ARM64 stack, resource
  allocation, PVC references and native loopback binding were preserved and checked
  through source, image contents and rendered manifests.
- Helm/Argo migration, live PVC reattachment and rollback were prepared in
  [the migration procedure](../operations/inference-migration.md), not performed.
- Hosted GitHub Actions were updated and tested through local workflow/provenance
  regressions; no remote CI run or release publication was triggered.
- No full six-case manual quality assessment or throughput benchmark was performed.
  Passing output validation and real smoke tests does not establish review correctness.
- Existing dependency deprecation notices and the frontend chunk-size notice remain;
  they did not fail checks.

For reproducible setup, project-specific gates and request/lifecycle semantics, see
[the Thrift guide](../guides/apache-thrift.md). Real tests require explicit opt-in
and an independently running service.
