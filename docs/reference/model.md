# Model configuration

[Documentation index](../README.md)

## Ollama inference

The backend and evaluator use **Ollama only**. Python settings and Helm default to the in-cluster service,
using `qwen3:1.7b` Q4_K_M at
`http://review-ollama.local-inference.svc.cluster.local:11434`. Install the
[independent GPU Ollama release](../../deploy/helm/local-ollama/README.md) first.
Direct host development explicitly overrides the URL to `http://localhost:11434`
in `.env.example`; cluster DNS is intended for Pods. The backend accepts local and
fully qualified cluster Service HTTP endpoints on port 11434, and disables proxy
environment variables and redirects.
`OLLAMA_MODEL_DIGEST` optionally pins a manifest digest; the maintained overlay pins
`sha256:8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7`.
The loaded digest is checked before and after each review to reject changed tags.

Ollama uses the same three-section prompts, total output budget, quality validator,
and serial durable queue. `/api/chat` uses streaming internally to observe first
content latency and support cancellation; the browser still polls saved jobs.
`think=false` renders the stock template's `/no_think` control and empty thinking
prefix. Unexpected thinking/tool output is rejected. No reasoning is stored or logged.

Startup downloads only the pinned `tokenizer.json`. It uses `/api/show` to verify the
stock template's SHA-256, vocabulary, merges, architecture and size, then tests
one-token generation. All responses must report the same exact prompt token count as
local admission. `num_ctx` includes both the input and output budgets, preventing
context truncation for admitted requests. A custom template requires an explicitly
implemented and tested renderer; character-count estimates are never used.

Startup availability checks retry transient failures for up to 60 seconds; missing
models, incompatible identities and malformed metadata fail immediately.

Timeout/shutdown cancels the in-flight async HTTP operation and closes its connection
before the adapter exits. Ollama owns remote generation cleanup; the single worker
is not reused while the local operation remains active. Connection, missing-model,
identity/tokenizer mismatch, timeout and malformed-stream failures produce explicit
errors with no fallback or model download. Subsequent submissions may recover when
the service is available; replacing the model digest requires a backend restart.

`/api/v1/runtime` identifies Ollama by actual tag/digest and quantization from its API.
Device means placement **observed at startup or the last completed review** from
`/api/ps`, not continuous GPU monitoring. Saved review `model_revision` contains the
Ollama `sha256:` digest; old HF revisions and simulated records are preserved.

`MODEL_BACKEND`, Helm `model.backend`, and CLI `--model-backend` have been removed.
There is no in-process Transformers implementation or automatic fallback. Existing
review history and old cache files are retained; this change does not delete data.

## Configuration and generation

| Setting | Default / behavior |
| --- | --- |
| OLLAMA_BASE_URL | In-cluster service above; override for local development |
| OLLAMA_MODEL | `qwen3:1.7b` |
| OLLAMA_MODEL_DIGEST | Optional in Python; maintained Helm/evaluator configuration pins the digest above |
| MODEL_ID / MODEL_REVISION | Tokenizer only: `Qwen/Qwen3-1.7B` at `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` |
| HF_HOME | `/models/huggingface`; retained tokenizer cache |
| MODEL_MAX_INPUT_TOKENS | 2048 including instructions and source |
| MODEL_MAX_OUTPUT_TOKENS | Python default 512; maintained deployment and evaluator default 384 |
| MODEL_INFERENCE_CONCURRENCY | 1; other values rejected |
| INFERENCE_TIMEOUT_SECONDS | Python default 180; deployment and evaluator default 300 seconds |
| INFERENCE_DRAIN_SECONDS | 30 seconds |
| QUEUE_CAPACITY / MAX_RETRIES | 8 queued jobs / 1 interruption retry |

The 384-token budget splits into 72 Summary, 176 Findings and 136 Suggestions tokens.
Other budgets use the same 9/22/17 weighted allocation, with at least one token per
section. All sections share one deadline. Admission counts the largest complete
section prompt using the verified tokenizer.

The single inference executor generates the `Summary`, `Findings`, and `Suggestions`
bodies sequentially from three short independent prompts. Summary is limited to two
short sentences; Findings and Suggestions are each limited to three concise Markdown
list items. The prompts require every sentence or item to end with `.`, `!` or `?`. They
prohibit copying source text or repeating material assigned to another section. If there
is no material issue, the model must say so explicitly instead of filling the token
budget. Each prompt repeats the original language hint and untrusted source and asks
only for that section body. No conversation history, prior generated section, or tool
result is appended. The backend inserts the three exact Markdown headings; it does not
invent, copy, or synthesize a review conclusion.

Each request uses temperature 0.7, top-p 0.8, top-k 20, min-p 0 and repeat penalty 1.
The shared generation policy is used by both production and evaluation. Each section
receives a stable 63-bit seed derived from the actual Ollama digest, language, section
and source. Startup uses seed 0 and one output token. Seeds are never logged or saved;
identical seeds do not guarantee identical output across engines or hardware.

The backend first normalizes surrounding whitespace and echoed headings in each body.
Only when a section generated at least its own token limit does the backend inspect its
ending. An ending with `.`, `!` or `?` is retained. A limited set of closing quotes,
backticks, brackets, parentheses and Markdown emphasis characters may follow that
punctuation. Otherwise, only the suffix after the last complete terminator is deleted,
preserving every earlier sentence or list item exactly. The backend never continues,
rewrites, summarizes, or infers model text. A capped section with no complete boundary
fails closed as `invalid_model_response` with the internal reason `truncated_section`;
non-capped sections are unchanged.

The backend then assembles the Markdown and runs the response quality gate. All sections must be nonempty and ordered, and the combined result must mention at least one distinctive ASCII identifier when the source provides one. This lightweight contract rejects obviously malformed or detached output; it does not prove that accepted findings are correct. Model ID and revision are written to every completed review; failed jobs retain a safe error instead of a fabricated result.

The public and persisted error is `invalid_model_response` with one generic message. Internally, `review_finished` may add one fixed `validation_reason`: `missing_sections`, `section_order`, `empty_section`, `unexpected_section_heading`, `detached_source`, or `truncated_section`. It never includes a section name, missing heading name, source identifier, or model text.

The adapter emits one `model_generation_finished` event for each attempted review.
It reports per-section token counts, first-content and generation latency, limit flags
and capped-tail removal flags. Local preparation and PyTorch thread metrics are null
because those operations run in Ollama. No prompts, source, output, token IDs or
reasoning are logged. Process RSS in the evaluator measures its HTTP client, not the
Ollama server or GPU memory.

## Dependencies and verification

The backend image installs Python 3.12, the application dependencies, `tokenizers` and
`huggingface-hub`. It does not install Torch, Transformers or safetensors. Ollama owns
model weights and its separate PVC. Backend startup may fetch only the pinned tokenizer;
the evaluator requires that tokenizer already cached and disables Hub downloads.

Run the optional real-service smoke test with an installed local model:

```bash
cd backend
OLLAMA_BASE_URL=http://localhost:11434 RUN_REAL_MODEL=1 .venv/bin/pytest tests/test_real_model.py -v
```

Its reduced 64-token budget permits a controlled quality rejection and does not prove
semantic quality. Use the [evaluation guide](../testing/model-evaluation.md) for the
synthetic suite. Dated [CPU measurements](../reports/verification-2026-09-10-to-13.md)
describe the removed Transformers implementation, not current Ollama performance.
