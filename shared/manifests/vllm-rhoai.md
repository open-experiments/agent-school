# Model endpoint options

Every example needs one OpenAI-compatible endpoint, set via `LLM_BASE_URL`,
`LLM_API_KEY`, `LLM_MODEL`.

## Option A: RHOAI Model-as-a-Service

If your organization runs the Red Hat AI gateway / MaaS, request a key and
use the provided base URL. The public Red Hat reference for the pattern:
https://github.com/rh-aiservices-bu/models-aas

## Option B: vLLM on OpenShift AI

Deploy a model with the vLLM ServingRuntime from the OpenShift AI dashboard
(single-model serving), then:

```
LLM_BASE_URL=https://<inference-route>/v1
LLM_MODEL=<deployed-model-name>
```

Distributed Inference with llm-d is available as Technology Preview in
Red Hat AI Inference 3.4 for scale-out serving; the examples do not require
it, and they benefit from it transparently when it fronts the same endpoint.

## Option E: Rome sandbox (SNO · RHOAI 3.5 EA2 · reference platform)

The curriculum's reference deployment: a single-node OpenShift 4.22 cluster
("rome") running RHOAI 3.5 EA2 with 2x RTX 4090D, serving
`Kimi-Linear-48B-A3B-Instruct` (AWQ 8-bit, tensor-parallel 2) through a
custom upstream-vLLM ServingRuntime, from weights staged in in-cluster MinIO
and registered in the RHOAI Model Registry (`rome-registry`).

External (laptop):

```
LLM_BASE_URL=https://kimi-linear-48b-a3b-telco-aix.apps.rome.narlabs.io/v1
LLM_API_KEY=none
LLM_MODEL=kimi-linear-48b-a3b
```

In-cluster (agent pods; the predictor Service is headless, so the port must
be explicit):

```
LLM_BASE_URL=http://kimi-linear-48b-a3b-predictor.telco-aix.svc.cluster.local:8080/v1
```

Operational notes, learned the hard way:

- The route carries `haproxy.router.openshift.io/timeout: 600s`; the
  OpenShift default of 30s truncates non-streamed completions.
- First request after a pod restart pays a ~60-80s Triton autotune cost for
  the KDA kernels; subsequent requests are fast.
- Tool calling requires the runtime args `--enable-auto-tool-choice
  --tool-call-parser=kimi_k2`.
- Wildcard DNS (`*.apps.rome.narlabs.io`) must resolve from your client, or
  pin the route host in `/etc/hosts`.

## Option C: local vLLM

```
python -m vllm.entrypoints.openai.api_server --model <hf-model> --port 8000
LLM_BASE_URL=http://localhost:8000/v1
```

## Option D: local llama.cpp (recommended for laptop dev)

`llama-server` exposes the same OpenAI-compatible API; `--jinja` enables
tool calling with models whose chat template supports it (Qwen, Hermes,
Llama 3.x instruct):

```
llama-server -m <model>.gguf --port 8080 --jinja
LLM_BASE_URL=http://localhost:8080/v1
LLM_MODEL=<model-name>
```

On Intel Macs with AMD GPUs, build llama.cpp with Vulkan on and Metal off
(`cmake -B build -DGGML_VULKAN=ON -DGGML_METAL=OFF`); the full recipe is our
[macintelamd-ai-enablement](https://github.com/open-experiments/Telco-AIX/tree/main/macintelamd-ai-enablement)
guide. Apple Silicon Macs use the default Metal build (`brew install llama.cpp`).
