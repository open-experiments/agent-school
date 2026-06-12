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
