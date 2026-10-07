# Model configuration

[Documentation index](../README.md)

## Active backend selection

Python settings and Helm default to **in-cluster Ollama** (`MODEL_BACKEND=ollama`),
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

Use Helm `--set model.backend=transformers` or `MODEL_BACKEND=transformers` for the
explicit CPU path. See [deployment commands](../guides/helm-release.md).

## Transformers CPU reference

The following loading, weight-cache and tokenizer details describe the retained
Transformers adapter. Its historical measurements are not Ollama benchmarks.

The Transformers review model is [`Qwen/Qwen3-1.7B`](https://huggingface.co/Qwen/Qwen3-1.7B), pinned to Hugging Face commit [`70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`](https://huggingface.co/Qwen/Qwen3-1.7B/tree/70d244cc86ccca08cf5af4e1e306ecf908b1ad5e). Hugging Face's official metadata resolved that exact revision on 2026-09-12. The model is licensed under Apache-2.0. Its two BF16 safetensors files and locally verified SHA-256 values are:

| File                               | SHA-256                                                            |
| ---------------------------------- | ------------------------------------------------------------------ |
| `model-00001-of-00002.safetensors` | `169ad53ec313c3a34b06c0809216e4fc072cce444a5d4ff2b59690d064130ed5` |
| `model-00002-of-00002.safetensors` | `912becff8d60672aa8628ef08c05898d9adf17c2ad4ae3caf99b065622fdeff9` |

The API exposes no model selector and there is no external inference fallback. The backend rejects other model IDs, but its `MODEL_REVISION` setting currently validates only 40-character lowercase hexadecimal syntax, not equality to the pinned commit. The maintained manifest and acceptance checks enforce the documented revision; a custom environment override is not an approved model configuration. Existing completed SQLite reviews retain their recorded model ID and revision without migration.

| Setting                     | Default / required behavior                                                           |
| --------------------------- | ------------------------------------------------------------------------------------- |
| MODEL_ID                    | Qwen/Qwen3-1.7B; other IDs rejected                                                   |
| MODEL_REVISION              | Maintained minikube deployment pins the commit above; backend default is the same     |
| HF_HOME                     | /models/huggingface, dedicated retained local model-cache PVC                         |
| MODEL_DTYPE                 | bfloat16; float32 available for CPU compatibility testing                             |
| MODEL_MAX_INPUT_TOKENS      | 2048 including instructions and source                                                |
| MODEL_MAX_OUTPUT_TOKENS     | Backend default 512; minikube override 384                                            |
| MODEL_INFERENCE_CONCURRENCY | 1; other values rejected                                                              |
| MODEL_CPU_THREADS           | 2 (PyTorch intra-op threads)                                                          |
| OMP_NUM_THREADS             | Minikube container sets 2; direct-development environments must be checked separately |
| INFERENCE_TIMEOUT_SECONDS   | Backend default 180; minikube override 300 seconds                                    |
| INFERENCE_DRAIN_SECONDS     | 30                                                                                    |
| MAX_RETRIES                 | 1 interruption retry                                                                  |
| QUEUE_CAPACITY              | 8 queued jobs                                                                         |

The minikube ConfigMap limits one complete review to 384 generated tokens and allows 300
seconds for the complete CPU inference job. The deployment budget is split
deterministically into 72 Summary tokens, 176 Findings tokens, and 136 Suggestions
tokens. For other valid budgets, each section receives at least one token. The remaining
tokens are divided using 9/22/17 weights and a deterministic largest-remainder
allocation. This assigns every token, so the three section limits always add up to the
supplied total. All three calls share one monotonic deadline and the coordinator's
existing whole-job timeout. A stop signal terminates the current cooperative generation
and prevents later sections from starting. The backend defaults remain 512 tokens and
180 seconds for direct-development configurations; deployment settings are supplied
through environment variables.

The model loads once on CPU with `trust_remote_code=False`, `use_safetensors=True`, and `eval()`, then performs a one-token validation inside `torch.inference_mode()`.

Cache validation checks a readable single `model.safetensors` file when no index exists.
When an index exists, it must be valid. Every referenced shard must be readable, have
structurally valid safetensors metadata and contain exactly the tensor names listed for
it in the index. A broken index or missing shard cannot fall back to accepting another
weight file; validation does not load full tensors or perform a complete weight-content
SHA-256 audit. Startup checks the exact pinned local snapshot first and otherwise
downloads only safetensors, index/config, and tokenizer assets. Readiness stays false
until the model, DynamoDB initialization check, and writable SQLite storage are ready.
Liveness remains available during loading.

Token counts use the exact tokenizer and the largest of the three complete section prompts. Production uses one fixed system message containing the safety instructions and one user message containing the section request and untrusted source. Source text cannot create additional messages or roles. Qwen3's tokenizer chat template is invoked with `tokenize=False`, `add_generation_prompt=True`, and the hard `enable_thinking=False` switch. The application does not use the `/no_think` text control and does not request, parse, save, or log chain-of-thought. The safe explicit plain-text fallback remains available if a pinned tokenizer lacks a template.

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

Qwen recommends `do_sample=true`, temperature 0.7, top-p 0.8, top-k 20, and min-p 0 for non-thinking mode. Review and one-token startup generation pass exactly those values. No presence penalty or substitute parameter is added. EOS behavior remains inherited from the pinned model generation config, while padding uses the tokenizer's actual `pad_token_id` and generation caching remains enabled.

Each review section receives a stable, nonnegative 63-bit seed. It is derived with
SHA-256 from the model revision, language, fixed section name and source, with each
value encoded alongside its length. The seed is applied only to the default CPU
generator inside a fresh `torch.random.fork_rng(devices=[])` context, which restores the
prior CPU RNG state afterward and never calls a CUDA RNG. Startup validation uses a
separate fixed non-sensitive seed under the same isolation. Seeds and source-derived
hashes are neither logged nor persisted. This supports repeatable sampling for the same
input on the same fixed software and hardware stack; PyTorch does not guarantee
bit-for-bit reproducibility across releases, platforms, or CPU architectures.

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

The Transformers path emits one review-level `model_generation_finished` event before final response validation. Its monotonic `duration_ms` sums time spent in the three generation calls, and `generated_tokens` sums each output sequence length minus its input sequence length. The event also contains the global output limit and flag plus fixed-key per-section token counts, limit flags, and `section_trailing_fragments_removed` booleans. These content-free metrics do not include removed fragments, character counts, punctuation, token IDs, decoded output, prompts, source, or chain-of-thought.

Historical model acceptance, measured latency/RSS, rejected candidates and owner-supplied observations are retained in the [model evaluation report](../reports/verification-2026-09-10-to-13.md). Those results do not revalidate the current checkout or establish the current minikube memory fit. Every generated finding requires human verification.

The backend Dockerfile uses Python 3.12 and a pinned PyTorch 2.8.0 CPU wheel; Transformers/Hugging Face dependencies are separately locked. The model dependency lock pins Transformers 4.57.6; inspect the actual runtime separately rather than inferring an installed version from the lock. Model downloads never occur during ordinary unit tests or Docker builds. Large model weights remain on the 12 GiB runtime cache PVC or ignored local cache. Cached models are not deleted automatically. Check actual PVC usage before rollout and allow space for Hub metadata and temporary download files; the configured capacity alone does not establish sufficient free space.

Run the opt-in smoke test:

```bash
cd backend
RUN_REAL_MODEL=1 .venv/bin/pytest tests/test_real_model.py -v
```

The test verifies actual model loading, tokenization, inference/evaluation mode and CPU
placement. With its deliberately small 64-token budget, it accepts either a review that
meets the output contract or a controlled rejection by the response quality gate. It is
not a semantic acceptance suite or a minikube latency/memory benchmark. See
[verification](../reports/verification-2026-09-10-to-13.md) for the controlled
six-fixture result.

## Evaluation evidence

The [model evaluation guide](../testing/model-evaluation.md) distinguishes the reduced-budget smoke test, current fixed-suite quality acceptance and historical selection. New synthetic fixtures are not recovered historical inputs; see the [material audit](../reports/model-evaluation-materials-2026-09-25.md). No new real-model evaluation was run when the tool was added on 2026-09-25.
