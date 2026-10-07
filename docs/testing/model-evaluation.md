# Current-model evaluation through Ollama

The evaluator uses the same `OllamaModel`, prompts, three serial section requests,
seed policy and output validator as the application. It does not run a separate
Transformers model. Run commands from the repository root.

## Preparation

Install the backend development environment and start Ollama 0.24.0 or newer
with the chosen local model already installed. No tokenizer cache or optional model
Python dependencies are needed. The evaluator never downloads a model.

The default endpoint is `http://localhost:11434`, and the default model is
`qwen3:1.7b`. Use `--ollama-base-url`, `--ollama-model` and optionally
`--ollama-model-digest` to select another installed model. Without an explicit digest,
the adapter captures it at startup and checks it throughout the run.
All validation and sampling defaults come from the production model settings.

## Plan and run

```bash
backend/.venv/bin/python scripts/evaluate_model.py --dry-run
backend/.venv/bin/python scripts/evaluate_model.py --run-real-model --case average
backend/.venv/bin/python scripts/evaluate_model.py --run-real-model --case all
```

Omitting the mode flags also produces a plan. Plan mode does not construct a model,
contact Ollama, load model dependencies or download anything. Exit 0 with
`status=not_run` means a valid plan, not an inference pass.

Defaults match the maintained deployment's 2048 input tokens, 384 total output tokens
and 300-second review deadline. Use `--max-output-tokens` and `--timeout-seconds` to
match another application configuration; the report records both. Settings ignore
ambient environment overrides and `.env`. Model, endpoint, digest, context and sampling are explicit CLI
choices; use `--help` for all options. All three sections share the production deadline and budget allocation.

To inspect generated synthetic output and record human judgments in a private terminal:

```bash
backend/.venv/bin/python scripts/evaluate_model.py --run-real-model --case all --review-in-terminal
```

Output is escaped and displayed through `/dev/tty`, not written to reports. For each
judgment, `y` means passed, `n` failed, and anything else or EOF leaves it pending.
No automatic confirmation or keyword score can substitute for semantic review.

## Acceptance contract

The checked-in `fixtures-v1.json` contains six synthetic cases: `hello_world`, `average`,
`square`, `first_item`, `sql_injection` and `prompt_injection`. They are a new baseline,
not reconstructed historical inputs. The evaluator parses but never executes them.
No arbitrary user-source input option exists.

Every case must pass output structure/source-binding validation, generation metrics
and runtime budget checks. Ollama does not report local preparation or PyTorch thread
metrics, so those fields are not required. Each section must report valid input/output
token counts, first-content/generation latency and completion/limit flags.

Human review must confirm semantic correctness, no fabricated findings, injection
resistance, no claims of execution and complete endings. Concept/claim/punctuation
hints are diagnostics only. A production quality rejection fails evaluation.

| Status / exit | Meaning |
| --- | --- |
| `not_run` / 0 | Plan only |
| `passed` / 0 | All selected cases passed automatic and human checks |
| `needs_manual_review` / 3 | Automatic checks passed; semantic judgments pending |
| `failed` / 1 | Dependency/service/runtime/quality/metrics/manual failure |
| Argument error / 2 | Invalid or incompatible options |

The opt-in backend smoke test uses a smaller budget and permits controlled output
rejection. It establishes transport/inference behavior, not quality acceptance.

## Reports and supervision

Each run writes a new private `.local/model-evaluations/<run>/report.json` (directory
0700, file 0600). Schema/tool version 3 records source/fixture/dependency fingerprints,
configuration, actual Ollama digest, quantization and observed device after loading.
Plan mode leaves unobserved device/quantization null. Compare schema, fingerprints,
model digest and budgets before comparing runs with historical reports.

Reports contain typed metrics and categorical judgments, never source, prompts, output,
seeds, credentials or arbitrary exception messages. Generation/first-content timings
are milliseconds; load/review wall times are seconds. Process RSS is the evaluator
client's lifetime high-water mark: it excludes Ollama weights, server and GPU memory.

The parent supervises one serial HTTP client worker. Load/preflight has a 600-second
watchdog; review has its configured timeout plus 30 seconds to stop. The parent can
terminate only its worker, not the external Ollama service. Adapter cancellation closes
the HTTP connection; Ollama owns remote cleanup. There is no inference retry or fallback.
Manual reading time is excluded from inference timing.

Historical [CPU reports](../reports/verification-2026-09-10-to-13.md) describe the removed
Transformers path and do not establish current Ollama quality or performance.
