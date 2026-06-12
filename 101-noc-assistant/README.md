# 101 · NOC Assistant

> **QA status:** offline mode, MCP servers, mock loop, and two live runs
> against Red Hat OpenShift AI Model-as-a-Service (LiteLLM gateway,
> Qwen3.6-35B-A3B) all pass. Full evidence, including wire-level MaaS
> request/response captures, lives in [QA/](./QA/).

A single agent that answers NOC questions about a 5G core: it inspects telemetry, detects anomalies, and consults runbooks before answering. The "hello world" of agentic telco AI, except it already does real work.

**Source experiments:** [5gprod](https://github.com/open-experiments/Telco-AIX/tree/main/5gprod) (telemetry + NOC workflow), [telco-sme](https://github.com/open-experiments/Telco-AIX/tree/main/telco-sme) (domain knowledge access).

## What it teaches

1. The agent loop: plan, call a tool, observe, repeat, answer.
2. Tools as MCP servers: the agent's capabilities live outside the agent.
3. The model as a stateless service: any OpenAI-compatible endpoint works, vLLM on RHOAI is the reference path.
4. Tracing: every step is printed as a structured trace and mirrored to MLflow when configured.

## Architecture

![101 NOC Assistant architecture](./images/architecture.png)

The agent pod is a CPU-only loop. Its capabilities live outside it as two MCP
servers reading the real 5gprod dataset, and the model is whatever
OpenAI-compatible endpoint you point at: llama.cpp for local dev, vLLM on
RHOAI as the target. The OpenClaw track swaps the loop without touching the
tools.

## Blueprint mapping

| Blueprint component | Here |
|---------------------|------|
| Harness | `agent/noc_agent.py` custom tool-calling loop (any OpenAI-compatible endpoint) |
| Model | your endpoint via `LLM_BASE_URL` (vLLM / RHOAI MaaS) |
| Skills (thin clients) | `tools/lib.py` functions (Isolation Forest, alert feed, runbooks) |
| Skill exposure | `tools/telemetry_mcp.py`, `tools/runbook_mcp.py` (MCP servers) |
| Observability | stdout trace + optional MLflow |

The classic-AI-then-GenAI chaining mirrors the source experiment: Isolation
Forest finds the anomalies, the alert feed scopes the incident, and the
language model reasons over both before answering.

## Run it

```bash
pip install -r requirements.txt

# Offline first: scripted episode over the real dataset, no endpoint needed
python agent/noc_agent.py --offline "What is wrong in the core right now?"

# Live: against any OpenAI-compatible endpoint
cp .env.example .env                      # set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
python agent/noc_agent.py "What is wrong in the core right now?"
```

To run the tools as standalone MCP servers (for MCP-native harnesses or a gateway in front):

```bash
python tools/telemetry_mcp.py             # stdio MCP server
python tools/runbook_mcp.py
```

## Data

`data/` carries the real Telco-AIX 5gprod dataset: 24 hours of per-minute
KPIs for AMF (registration, NAS/NGAP success, N1/N2 messaging), SMF
(session establishment, PFCP/N4 messaging, policy installation) and UPF
(packet processing, tunnels, throughput, drops, latency), plus a structured
alert feed with REGISTRATION_STORM, RESOURCE_EXHAUSTION and
SESSION_MANAGEMENT_FAILURE incidents and their before/during KPI deltas.
Source: [Telco-AIX/5gprod](https://github.com/open-experiments/Telco-AIX/tree/main/5gprod),
published at [huggingface.co/datasets/fenar/5gcore-prod](https://huggingface.co/datasets/fenar/5gcore-prod).
The runbooks in `runbooks/` are written against those three real alert types.

## Harness tracks

The agent above is the teaching harness: a custom loop you can read top to
bottom. The same skills run unchanged under product harnesses:

- [OpenClaw track](./harness-tracks/openclaw.md): register the two MCP
  servers and the RHOAI endpoint in `~/.openclaw/openclaw.json` and ask the
  same question through OpenClaw's own always-on loop. No agent code involved.
  The track also covers continuous 7/24 operation via OpenClaw heartbeat:
  periodic anomaly/alert sweeps that pick up fresh data from `data/`
  automatically, alerting a channel only when something needs attention.

## Deploy on OpenShift

UBI9 image, restricted-PSS manifests, Job for one-shot questions and a
suspended CronJob for the continuous sweep: see [deploy/](./deploy/).

## Where this goes next

In 201 the runbook lookup becomes a real RAG skill backend; in 301 this same diagnostic capability becomes one worker in a closed loop. The agent code barely changes; the platform around it grows. That is the point.
