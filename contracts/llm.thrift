// v1: field IDs and enum values are permanent. See README.md for compatibility.
namespace py llm_contract.generated
namespace java demo.llm.generated

enum GenerationState {
  QUEUED = 1,
  RUNNING = 2,
  CANCELLING = 3,
  SUCCEEDED = 4,
  FAILED = 5,
  CANCELLED = 6
}

struct Message {
  1: required string role,
  2: required string content
}

struct GenerationOptions {
  1: optional i32 max_output_tokens,
  2: optional double temperature,
  3: optional double top_p,
  4: optional i64 seed
}

struct Limits {
  1: required i32 active_generations,
  2: required i32 pending_generations,
  3: required i32 retained_results,
  4: required i32 result_ttl_ms,
  5: required i32 max_deadline_ms,
  6: required i32 max_message_chars,
  7: required i32 max_input_tokens,
  8: required i32 max_output_tokens
}

struct ModelInfo {
  // A new UUID on every service process start; generation calls are fenced by it.
  1: required string instance_id,
  2: required string model_id,
  3: required string model_revision,
  4: required GenerationOptions defaults,
  5: required Limits limits,
  6: optional string quantization,
  7: optional string device
}

struct Health {
  1: required bool ready,
  2: required bool draining,
  3: required i32 active,
  4: required i32 pending
}

exception LlmError {
  // Stable machine-readable code; message never contains submitted/generated content.
  1: required string code,
  2: required string message,
  3: required bool retryable
}

struct GenerationRequest {
  1: required string request_id,
  2: required string instance_id,
  3: required list<Message> messages,
  4: optional GenerationOptions options,
  // Elapsed budget from admission, including queueing; unrelated to socket timeout.
  5: required i32 deadline_ms
}

struct TokenUsage {
  1: required i32 input_tokens,
  2: required i32 output_tokens
}

struct GenerationResult {
  1: required string content,
  2: required string model_id,
  3: required string model_revision,
  4: required TokenUsage usage,
  5: required string finish_reason,
  6: optional i32 generation_ms,
  7: optional i32 first_token_ms
}

struct GenerationSnapshot {
  1: required string request_id,
  2: required string instance_id,
  3: required GenerationState state,
  4: optional GenerationResult result,
  5: optional LlmError error,
  // Terminal results cannot be evicted before this Unix epoch time (milliseconds).
  6: optional i64 expires_at_ms
}

service LlmService {
  ModelInfo getModelInfo(),
  Health getHealth(),
  GenerationSnapshot startGeneration(1: GenerationRequest request) throws (1: LlmError error),
  GenerationSnapshot getGeneration(1: string instance_id, 2: string request_id)
    throws (1: LlmError error),
  GenerationSnapshot cancelGeneration(1: string instance_id, 2: string request_id)
    throws (1: LlmError error)
}
