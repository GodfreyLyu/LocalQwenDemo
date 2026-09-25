# Dated reports

Audience: maintainers evaluating prior observations. Purpose: preserve chronology, model/environment identity and limitations. Prerequisite: [current documentation](../README.md). A historic pass is not a current pass; a single completed review is not full environment or browser acceptance.

| Date | Report | Scope / limitation |
| --- | --- | --- |
| 2026-09-10–13 | [Verification baseline and follow-ups](verification-2026-09-10-to-13.md) | Selected Qwen evaluations and infrastructure verification; local/static and owner-supplied EKS evidence remain separate, with original results and limitations |
| 2026-09-20–21 | [Minikube diagnostics and repairs](minikube-maintenance-2026-09-20-to-21.md) | Historical blocked deployment, DynamoDB identity repair, model-cache repair and OpenMP summary; undated forwarding follow-up retained as such |
| 2026-09-21 | [Inference investigation](minikube-inference-investigation-2026-09-21.md) | Read-only investigation; root cause not established |
| 2026-09-21 | [CPU measurement A](minikube-cpu-measurement-2026-09-21.md) | One timed-out review; sampling gaps and host pressure qualify interpretation |
| 2026-09-21 | [CPU measurement B](minikube-cpu-b-measurement-2026-09-21.md) | One completed review with OMP=2; not a controlled causal/repeatability or full acceptance claim |
| 2026-09-25 | [Historical evaluation material audit](model-evaluation-materials-2026-09-25.md) | Missing historical six-case reproduction materials; new synthetic baseline and deterministic evaluator checks; no new inference |
| 2026-09-25 | [Local-only deployment refactor](local-only-refactor-2026-09-25.md) | Offline product/deployment regressions and fake-model browser checks; no live deployment or real inference |
| 2026-09-25 | [Documentation and check-entry maintenance](maintenance-2026-09-25.md) | This round's migration map, local validation and unexecuted environment work |

The original [A evidence](../evidence/minikube-cpu-measurement-2026-09-21.json) and [B evidence](../evidence/minikube-cpu-b-measurement-2026-09-21.json) have not moved or changed. Ignored `.local` artifact references in old reports may only resolve on the original machine. New reports follow the [evidence rules](../testing/README.md#evidence-and-manual-acceptance).

Cloud portions of mixed historical reports are archival only. Their infrastructure,
commands and deployment capabilities are retired and are not current operating guidance.
Local measurements, original results, limitations and raw JSON evidence remain intact;
use the current README/minikube guide for operations. Provider/configuration files ignored
by Git on older workstations were not erased by removing tracked cloud source files.
