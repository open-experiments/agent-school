# Shared

Cross-example infrastructure.

- `manifests/vllm-rhoai.md`: model endpoint options the examples point at
  (RHOAI Model-as-a-Service, vLLM on OpenShift AI, local vLLM, local
  llama.cpp for laptop dev).
- `manifests/ocp/secret-llm.example.yaml`: template for the one
  `llm-credentials` Secret every agent Job consumes via `envFrom`; create it
  once per namespace, never commit a filled-in copy (`keys/` and `.env` are
  gitignored).
- MCP conventions: every example exposes its skills as MCP servers over
  stdio; an MCP gateway (Kuadrant MCP Gateway or agentgateway) can front
  them for claims-based tool authorization without touching example code.
