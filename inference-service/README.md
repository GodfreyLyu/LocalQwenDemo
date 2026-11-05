# Ollama LLM service

Independent Python project and image. It accepts generic Thrift messages, supervises
its own Ollama child and keeps native HTTP on loopback. It has no backend, database,
authentication or code-review dependency.

`Dockerfile` and `mesa-krunkit.repo` define the pinned Fedora/Ollama/Mesa Vulkan
image. Build from the repository root so the image can also install `contracts`:

```bash
docker build --platform linux/arm64 -f inference-service/Dockerfile -t review-inference:local .
```

Start with [the Thrift guide](../docs/guides/apache-thrift.md) for installation,
configuration, request tracing, tests, builds and limits. The `.env.example` lists
service policy. `REQUIRE_GPU=true` is the deployment default; CPU development must
explicitly disable it. Model pulls are disabled for host development and enabled
for the Helm chart's first install. A mismatched installed revision is never replaced.

`src/llm_service/{runtime,lifecycle,handler,server,ollama}.py` separate lifecycle,
wire handling, admission and native execution. Tests run using this project's own
venv and `pytest.ini`, even without the backend installed. Kubernetes configuration
stays in `deploy/helm/local-ollama` to preserve release/PVC ownership; see
[migration](../docs/operations/inference-migration.md).
