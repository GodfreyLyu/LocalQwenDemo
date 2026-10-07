# Dated reports

These reports retain model evaluations, incident findings and measured acceptance
results. Each applies only to its recorded revision, environment and date. Use the
[current documentation](../README.md) for operating instructions; historical results
do not validate the current checkout.

| Date | Report | Scope and limits |
| --- | --- | --- |
| 2026-09-12 | [Historical model evaluations](verification-2026-09-10-to-13.md) | Qwen3 selection, output-quality follow-up and rejected Qwen2.5 candidate; local results and owner-supplied EKS observations remain distinct |
| 2026-09-20–21 | [Minikube incident findings](minikube-maintenance-2026-09-20-to-21.md) | Resource blockers, DynamoDB permissions, model-cache repair and an undated port-forward incident |
| 2026-09-21 | [Inference investigation](minikube-inference-investigation-2026-09-21.md) | Read-only diagnosis before the A/B measurements; root cause not established |
| 2026-09-21 | [CPU measurement A](minikube-cpu-measurement-2026-09-21.md) | One timed-out review, with observation gaps and host memory pressure |
| 2026-09-21 | [CPU measurement B](minikube-cpu-b-measurement-2026-09-21.md) | One completed review with OMP=2; no controlled causal, repeatability or full acceptance claim |
| 2026-09-25 | [Evaluation material audit](model-evaluation-materials-2026-09-25.md) | Missing historical reproduction materials and the provenance of the new synthetic baseline; no new inference |
| 2026-10-03 | [Ollama integration acceptance](ollama-integration-2026-10-03.md) | Host Ollama integration, API and browser results; network isolation failed verification, and timings are not a controlled benchmark |

The original [A evidence](../evidence/minikube-cpu-measurement-2026-09-21.json) and
[B evidence](../evidence/minikube-cpu-b-measurement-2026-09-21.json) are unchanged.
Ignored `.local` paths in historical reports may exist only on the original machine.
One-time maintenance logs and retired cloud deployment records remain in Git history.

Add a report when it preserves distinct measurements, acceptance results or useful
incident findings. Routine formatting, documentation moves and per-change test logs
belong in commit or PR descriptions. Follow the [evidence rules](../testing/README.md#evidence-and-manual-acceptance)
and keep private runtime data out of Git.
