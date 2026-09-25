# Historical evaluation material audit — 2026-09-25

Audience: model maintainers investigating reproducibility. Purpose: separate recovered material from missing evidence before introducing a new baseline. Prerequisite: [current evaluator guide](../testing/model-evaluation.md). This audit is read-only with respect to all historical scripts/reports/cache. **No new real-model evaluation was executed.**

## Search scope and provenance

On 2026-09-25 (Asia/Tokyo), inspect the current production model/config/cache validator, real-model smoke test, model/testing references, preserved verification report, project-local evaluation-like Python/JSON/log/Markdown artifacts and all 17 reachable Git commits. No branch/worktree was switched or reset. Existing documentation maintenance edits were preserved. No applicable AGENTS.md was found.

Fixture-name searches in reachable tracked history found the verification document, but no tracked six-case runner, fixture definition or executable semantic evaluator. No additional six-case evaluation program/results fixture was found in project-local material. Third-party package examples are not project experimental evidence. Private runtime accounts/state and model tensor contents were not treated as fixture sources.

Related tracked implementation commits exist:

- `9eb7a788e23b7b457cdec3d4562b3f9aaec3e862`: Qwen3 replacement, 2026-09-12.
- `da316f523775249f0a0272bf5ebfb4716ba6d5c5`: output-tail change, 2026-09-12.

These associate code changes with reports; they do **not** establish exact runner revision, dirty files, full dependency/environment snapshot or semantic rule code used during each historical run.

## Six-case inventory

No exact original input is confirmed for the historical six-case runs. Do not silently substitute a similarly named current test or newly written snippet.

| Historical case | Original input found? | Judgment rules found? | Runner found? | Code version found? | Run configuration found? |
| --- | --- | --- | --- | --- | --- |
| `hello_world` | **Unconfirmed** for the historical six-case run | Prose expectations in six-case report; semantic rule code **missing** | Six-case runner **missing** | Related commits above; exact six-case runtime checkout **unproven** | Report gives Qwen3/revision, BF16, 2 threads, budgets and sampling; full execution manifest **missing** |
| `average` | **Unconfirmed** for the historical six-case run | Report expects empty-input division by zero; executable semantic rule **missing** | Six-case runner **missing** | Related commits only; exact runtime checkout **unproven** | Partial reported configuration; full manifest **missing** |
| `square` | **Missing** for the historical six-case run | Prose says source-bound/no fabricated issue; executable semantic rule **missing** | Six-case runner **missing** | Related commits only; exact runtime checkout **unproven** | Shared settings documented in report; per-run complete manifest **missing** |
| `first_item` | **Missing** | Prose expects empty-input indexing risk; executable rule **missing** | **Missing** | Related commits only; exact runtime checkout **unproven** | Shared report settings only; complete manifest **missing** |
| `sql_injection` | **Missing** | Prose expects string-interpolation injection detection; executable rule **missing** | **Missing** | Related commits only; exact runtime checkout **unproven** | Shared report settings only; complete manifest **missing** |
| `prompt_injection` | **Missing**, including the exact attack text | Prose expects ignored instruction and division-by-zero finding; executable rule **missing** | **Missing** | Related commits only; exact runtime checkout **unproven** | Shared report settings only; complete manifest **missing** |

The [historical verification report](verification-2026-09-10-to-13.md) retains per-case timing/tokens/results and limitations. Early Qwen3 selection used 384 total tokens split 64/192/128; the later capped-tail follow-up used 72/176/136, both with a 300-second deadline. Qwen2.5 candidate results refer to that model's own pin and parameters, not current Qwen3. The retained Qwen results and counts are unchanged. The report states output was inspected in memory and discarded; no saved output body or human adjudication artifact was found that would allow an independent semantic recheck today.

## New baseline and bounded validation

`scripts/evaluation/fixtures-v1.json` deliberately introduces new synthetic sources for all six IDs. They are newly authored here, not asserted to be historical inputs. `scripts/evaluate_model.py` evaluates only the current pinned production implementation with explicit offline opt-in. It records digests and separates automatic contracts from human judgments. The existing reduced-budget smoke test remains unchanged.

Actual deterministic validation for this addition:

| Check | Result |
| --- | --- |
| New evaluator regression | 39 passed, using model/cache/network doubles and private temporary reports; includes worker supervision, native-output suppression, CLI modes, manual-pending behavior and macOS/Linux RSS unit conversion |
| Existing minikube script regression | 207 passed through the shared script gate |
| Existing Shell fixture suites | Secret, EKS deployment and Terraform lifecycle suites passed using intercepted fake external commands |
| Backend suite | 129 passed, 1 opt-in real-model test skipped; two existing dependency deprecation warnings |
| Static checks | Project Ruff lint/format, Python syntax, shared Shell entry syntax, local Markdown paths/heading anchors and diff whitespace passed |
| CLI plans | Default mode, full-suite dry-run and single-case dry-run completed as `not_run`; no model dependency import/load/download |
| Preservation | Current production application/configuration, deployment manifests, original smoke test, prior reports/evidence unchanged relative to the start of that validation round |

Logs/JUnit are in `.local/verification/2026-09-25-evaluator/`. Plan-only reports under `.local/model-evaluations/` are explicitly `not_run`; they are not real-model acceptance and do not update any old experimental conclusion. The shared script gate used execution outside the sandbox only because its existing loopback tests require socket binding; all cloud/cluster commands remained fixtures. The evaluator's new tests passed inside the sandbox.

Not executed: real inference, weight downloads, dependency installation, Docker image builds/publication, frontend/browser reruns, live Kubernetes/AWS/Terraform operations, Secret changes, Git commit/push or reference-project changes. Product code and those environments were unchanged, so no environment acceptance was warranted or authorized. The historical reproduction gaps remain unresolved rather than filled with inferred inputs.
