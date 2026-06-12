# 101 · QA Evidence Pack

Everything in this folder is captured output from real runs against the real
5gprod dataset; nothing is hand-written. Live runs hit Red Hat OpenShift AI
Model-as-a-Service (LiteLLM gateway) with model `Qwen3.6-35B-A3B`.

## Test matrix

| # | Test | Evidence | Result |
|---|------|----------|--------|
| 1 | Offline scripted episode (no LLM) | `offline_run.log` | PASS |
| 2 | MCP servers via fastmcp client (4 tools invoked) | `mcp_smoke_test.log` | PASS |
| 3 | Live agent vs MaaS, broad NOC question | `live_run1_trace.log` + `live_run1_maas_wire.jsonl` | PASS |
| 4 | Live agent vs MaaS, narrow ranking question | `live_run2_trace.log` + `live_run2_maas_wire.jsonl` | PASS |

A fifth test, the live loop against a deterministic mock endpoint (validates
tool-call plumbing without model variance), runs in CI fashion via the
snippet at the bottom of this file.

## Wire logs: full MaaS request/response capture

`*_maas_wire.jsonl` files contain every HTTP request and response between
the agent and the model endpoint, one JSON record per line, captured by
setting `LLM_WIRE_LOG` (see `agent/noc_agent.py:_make_client`). The
`Authorization` header is redacted; everything else is verbatim, including
the tool schemas offered, the model's `tool_calls`, the tool results fed
back, and token usage.

Excerpt from `live_run1_maas_wire.jsonl` (turn 1):

- **Request** `2026-06-12T15:43:11Z` →
  `POST /v1/chat/completions`, model `Qwen3.6-35B-A3B`, 4 tools offered
  (`get_kpi_summary`, `detect_anomalies`, `get_active_alerts`,
  `search_runbooks`), user question: "What is wrong in the 5G core right
  now, and what should the NOC do about it?"
- **Response** `200` → the model emitted three parallel tool calls:
  `get_kpi_summary({"nf": "all", "window_minutes": 60})`,
  `get_active_alerts({"component": "all"})`,
  `detect_anomalies({"nf": "all", "contamination": 0.02})`.
  Usage: 689 prompt + 228 completion tokens.

Run 1 totals: 6 requests / 6 responses, 6 tool calls over 3 turns; final
answer is a prioritized NOC action plan grounded in the alert feed and the
runbooks (see end of `live_run1_trace.log`).

Run 2 totals: 4 tool calls over 4 turns with a different tool plan (no
runbook search; the question did not need remediation). Note the model
nondeterminism across runs: run 2 ranked UPF worst on resource KPIs, an
earlier identical question ranked AMF worst on registration KPIs. Both are
defensible readings of the same data; this variance is exactly why Table-3
of the article keeps deterministic checks (offline mode, mock loop) in the
matrix alongside live runs.

## Reproduce

```bash
export LLM_BASE_URL=https://litellm-litemaas.apps.prod.rhoai.rh-aiservices-bu.com/v1
export LLM_API_KEY=sk-...           # from your MaaS key (starts with sk-)
export LLM_MODEL=Qwen3.6-35B-A3B
export LLM_WIRE_LOG=QA/my_wire.jsonl
python3 agent/noc_agent.py "What is wrong in the 5G core right now?"
```

Deterministic mock-loop test (no endpoint, validates the loop plumbing):
run the snippet in `mock_live_loop.md`.

## Known findings from QA

1. Fixed during QA: `telemetry_mcp.py` carried stale tool signatures and was
   missing `get_active_alerts` (caught by test 2).
2. The key file `keys/maas.txt` was missing the leading `s` of the `sk-`
   key; corrected at runtime, file should be fixed and the key rotated.
