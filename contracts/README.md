# Shared LLM contract

`llm.thrift` is the source of truth. `python/llm_contract/generated` and
`java/demo/llm/generated` are reproducible outputs of Apache Thrift 0.25.0.
Python applications install this directory as the `local-llm-contract` package;
Java applications use the Maven library [`demo.llm:llm-client:0.1.0`](../clients/java/README.md),
which compiles these existing sources with `libthrift:0.25.0` and includes a small handwritten SDK.

Run `python3 scripts/generate_thrift.py` from the repo root; `--check` compares both
languages byte-for-byte. The Java generation suppresses timestamp annotations so
output is reproducible. [The guide](../docs/guides/apache-thrift.md) describes the
wire stack, error codes, request lifetime and independent project setup.

Compatibility rules:

- Never reuse, renumber or change the wire type/meaning of a field ID or enum value.
- Add optional fields with new IDs; keep existing required fields. Readers skip
  unknown fields; absence of an optional option means the service default.
- Add RPC methods without changing old signatures. New required fields, renamed
  methods and semantic changes require a new service/version and coordinated rollout.
- Unknown enum values/error codes must fail safely. A new server must preserve the
  semantics old clients rely on, including cancellation and retention guarantees.
- The earlier uncommitted review-specific IDL was never a released generic v1.
  It is wire-incompatible and has been replaced; backend and service must migrate
  together. Future generic v1 changes follow the rules above.

Requests have no application type, source-language field, review heading or output
validation. Supported message roles are `system`, `user` and `assistant`. Supported
options are output token cap, temperature, top-p and a nonnegative signed i64 seed.
The service has one pinned model per process; applications can assert the returned
identity without reproducing its runtime configuration.
