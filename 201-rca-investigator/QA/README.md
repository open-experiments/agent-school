# 201 · QA Evidence Pack

Captured output from real runs: real 5gprod corpus (4,330 documents: 4,323
telemetry rows + 7 alerts), real RAG service backend, live runs against Red
Hat OpenShift AI Model-as-a-Service (LiteLLM gateway, `Qwen3.6-35B-A3B`).

## Test matrix

| # | Test | Evidence | Result |
|---|------|----------|--------|
| 1 | RAG service: corpus build + retrieval relevance | `rag_service_smoke.log` | PASS (4330 docs; "registration storm" → `alert-0` top hit, score 0.43+) |
| 2 | Offline two-phase episode (no LLM) | `offline_run.log` + `../reports/rca_20260612_155739.md` | PASS |
| 3 | Live two-phase run vs MaaS | `live_run_trace.log` + `live_run_maas_wire.jsonl` + `../reports/rca_20260612_160342.md` | PASS |
| 4 | Live two-phase run vs Rome sandbox (self-hosted vLLM) | `rome_rca_trace.log` + `rome_rca_wire.jsonl` + `../reports/rome_rca_report.md` | PASS |
| 5 | Rome in-cluster run with MLflow Experiments tracking (workspace-scoped, SA-token auth) | `rome_mlflow_tracked_run.log` | PASS |

## Rome sandbox live run (test 4)

Same two-phase loop against the curriculum's Option-E reference platform
(`shared/manifests/vllm-rhoai.md`): `Kimi-Linear-48B-A3B-Instruct` AWQ
8-bit on upstream vLLM 0.25.1, TP=2, RHOAI 3.5 EA2 SNO. Single-model
configuration (`LLM_MODEL_SMALL` = `LLM_MODEL_LARGE` = the Kimi endpoint);
the small-vs-large routing story stays testable by pointing the two envs at
different registry deployments. 4 tool calls in phase 1 (incident fetch +
parallel evidence retrievals against the RAG backend), phase 2 produced a
structured RCA where every evidence entry carries its record id
(`alert-0`..`alert-6`) with KPI deltas — see
`../reports/rome_rca_report.md`. Prerequisite for tool calling on this
stack: the tokenizer fix documented in
`../../101-noc-assistant/QA/README.md` (Rome section).

## Experiments tracking run (test 5)

In-cluster Job (`rca-mlflow-1`, ServiceAccount `rca-investigator`) with the
rome overlay's `mlflow-tracking` ConfigMap: both phases exported as traces —
2 phase-1 investigation calls (4 tool calls against the RAG backend) and
1 phase-2 report write — visible in the RHOAI dashboard under
**Experiments → 201-rca-investigator** (3 traces, ~15K tokens, 0 errors,
per-trace latency). The report cited all seven alert records. Log:
`rome_mlflow_tracked_run.log`. Mechanics documented in the deploy README.

## Wire evidence (live run)

`live_run_maas_wire.jsonl`: 3 requests / 3 responses, auth redacted.

- **Phase 1** (2 requests, tools offered): the small model called
  `get_incident` then issued parallel `retrieve_evidence` queries against
  the RAG backend (AMF storm KPIs, SMF N4/session failures).
- **Phase 2** (1 request, no tools): evidence-only context, 7,115 prompt
  tokens, `chat_template_kwargs: {enable_thinking: false}` visible in the
  request body, 700-token report budget.
- **Artifact**: `reports/rca_20260612_160342.md`, a cited RCA where every
  claim carries record ids like `[alert-0]`.

## Findings from QA (fixed during the run)

1. **Retrieval relevance bug**: TF-IDF tokenized `registration_rate` and
   `REGISTRATION_STORM` as single tokens, so plain-word queries scored 0.
   Fixed with an underscore-splitting preprocessor in the RAG service.
2. **Hybrid-reasoning budget burn**: Qwen3 spent the entire `max_tokens`
   budget on `reasoning_content`, returning an empty report
   (`finish_reason: length`). Qwen's `/no_think` soft switch was not honored
   by the deployment; the working fix is vLLM's
   `chat_template_kwargs: {enable_thinking: false}` via `extra_body`,
   verified with a minimal probe before adoption. Thinking stays ON for
   phase-1 tool planning, OFF for the phase-2 write-up.
3. **Operational knobs added**: `RCA_MAX_TURNS` (investigation budget) and
   `RCA_REPORT_MAX_TOKENS` (report budget); both are cost/latency controls,
   not test shortcuts.

## Reproduce

```bash
pip install -r requirements.txt
uvicorn backend.rag_service:app --port 8201 &     # pattern-2 backend
python3 agent/rca_agent.py --offline               # deterministic episode
# live (set LLM_* envs; see .env.example):
export LLM_WIRE_LOG=QA/my_wire.jsonl
python3 agent/rca_agent.py "Produce an RCA for the most severe current incident."
```
