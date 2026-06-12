# 201 · RCA Investigator

> **QA status:** working. RAG backend (4,330 real documents), offline
> episode, and a live two-phase run against RHOAI MaaS (Qwen3.6-35B-A3B)
> all pass; the live run produced a fully cited RCA report. Evidence and
> wire traces in [QA/](./QA/).

A research agent that turns an anomaly into an evidenced root-cause report:
it retrieves correlated logs and metrics from a vector-store backend, reasons
over them, and writes an RCA where every claim cites a retrieved record.

**Source experiment:** [llm-rca](https://github.com/open-experiments/Telco-AIX/tree/main/llm-rca)
(Classic-AI → GenAI model chaining with FAISS log association), reusing the
5gprod dataset and the per-NF vector stores its `preprocess_data.py` builds.

**Harness:** custom two-phase loop (teaching; same OpenAI-compatible client
101 uses, here split into investigate and write phases); Hermes Agent track
planned (product harness over the same MCP skills).

## Architecture

![201 RCA Investigator architecture](./images/architecture.png)

## Solution flow

1. An anomaly event arrives (the same telemetry tools 101 built; the
   Isolation Forest output is the trigger).
2. The agent plans a retrieval: which NF, which incident window, which
   evidence it still lacks.
3. The retrieval MCP tool queries the RAG service backend, a separately
   deployed FAISS vector-store service (pattern 2); the index never lives
   inside the agent pod.
4. Inference is Tier-2 routed: a small model summarizes retrieved chunks
   cheaply, the large model writes the causal analysis.
5. The agent emits the RCA report to external storage, with citations back
   to the retrieved records, and the session ends clean.

## Blueprint mapping

| Blueprint component | Here |
|---------------------|------|
| Harness | `agent/rca_agent.py` two-phase loop in the pod (investigate → write) |
| Skill backend (pattern 2) | FAISS RAG service from llm-rca |
| Inference routing | small model for summaries, large for write-up |
| Ephemeral sessions | report externalized, no state in the loop |
| Observability | retrieval + reasoning steps traced |

## What it teaches

1. RAG as a skill backend, not an in-process index.
2. Cost-aware model routing inside one agent task.
3. Evidence discipline: ungrounded sentences are bugs.

## Run it

```bash
pip install -r requirements.txt
uvicorn backend.rag_service:app --port 8201 &   # RAG backend (pattern 2)

python3 agent/rca_agent.py --offline             # deterministic, no LLM
cp .env.example .env                             # then live:
python3 agent/rca_agent.py "Produce an RCA for the most severe current incident."
```

The report lands in `reports/` with record-id citations. Knobs:
`RCA_MAX_TURNS` (investigation budget), `RCA_REPORT_MAX_TOKENS` (report
budget), `RCA_DISABLE_THINKING` (Qwen hybrid-reasoning off for the write-up;
see QA/README findings). Requires `../101-noc-assistant/data/` (ships with
the repo) or set `DATA_DIR`.

The retriever is TF-IDF for dependency-light classrooms; the source llm-rca
experiment used OpenAI embeddings + FAISS, and swapping the `Retriever`
class is a backend-only change, which is the pattern-2 lesson.

For MCP-native harnesses (the Hermes track, or a gateway in front), the same
skills are exposed as a stdio MCP server:

```bash
python3 tools/retrieval_mcp.py
```

## Deploy on OpenShift

One image, two roles: the RAG backend as Deployment + Service, each RCA as
a Job calling it over Service DNS. See [deploy/](./deploy/).
