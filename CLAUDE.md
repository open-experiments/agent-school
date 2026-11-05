# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Agent School is a **teaching curriculum**, not a product: five self-contained courses (`101-`, `201-`, `202-`, `301-`, `302-`) that each rebuild a proven [Telco-AIX](https://github.com/open-experiments/Telco-AIX) experiment as a governed agent workload on Red Hat OpenShift AI. Plus a static GitHub Pages site (`docs/`) with an interactive lab portal that replays recorded cluster snapshots.

Two consequences that shape every change:

- **Readability is a feature.** The agent loops are deliberately small and heavily docstring'd because students read them. Don't refactor a loop into abstraction; don't silently drop the explanatory comments.
- **Claims carry evidence.** README/QA status lines ("QA-passed", "live on Rome + Venice") are backed by captured logs in `<course>/QA/`. Never upgrade a status claim without adding the corresponding captured evidence.

## Commands

Each course is independent — install and run from inside its directory.

```bash
# 101 / 201: offline deterministic episodes (no model endpoint needed)
cd 101-noc-assistant && pip install -r requirements.txt
python3 agent/noc_agent.py --offline "What is wrong in the core right now?"
python3 agent/noc_agent.py "..."                     # live: needs LLM_BASE_URL/LLM_API_KEY/LLM_MODEL

cd 201-rca-investigator && python3 agent/rca_agent.py --offline
uvicorn backend.rag_service:app --port 8201          # 201's RAG skill backend (pattern 2)

# 202: LangGraph triage, offline uses a documented stub scorer
cd 202-fraud-triage && pip install -r agent/requirements.txt
python3 agent/triage_agent.py --offline --cases 6

# MCP servers (stdio) — run standalone or under an MCP gateway
python3 tools/telemetry_mcp.py
```

301/302 have **no `--offline` mode**: they are cluster-resident (A2A over HTTP, Redis state store, Kuadrant-governed scorers, Kueue-admitted Jobs). Exercise them with the `smoke_*.py` / `*_smoke.py` clients next to each agent, which run as the Jobs in `deploy/ocp/rome/`.

### Site + tape regression suite

```bash
npm i playwright && npx playwright install chromium   # once; or set CHROMIUM_PATH
docs/tests/run.sh                                     # PORT=8777 by default
node docs/tests/portal-core.test.js                   # a single suite (needs BASE_URL + a server on it)
```

`run.sh` serves `docs/`, runs all six Playwright suites, checks video-duration sync, and asserts **all five tapes regenerate byte-identical** from `shared/tapes/venice-tape-raw.json.gz`. Run it before pushing anything under `docs/` or `shared/tapes/`.

```bash
python3 video/sync-durations.py --check               # site/tests/READMEs vs mp4 durations
python3 docs/portal/build-tapes.py 101 venice shared/tapes/venice-tape-raw.json.gz \
    101-noc-assistant/MANUAL.md docs/portal/tapes/101-venice.json
```

## Architecture

### The blueprint, enforced in code

Every course obeys the same rules (from the README's Conventions, and they are load-bearing, not aspirational): agent pods are **CPU-only** (models are served by vLLM/KServe, heavy skills are separate services or batch Jobs); every capability is exposed as an **MCP tool** so authorization can move to a gateway without code changes; one agent = one process = one identity; **sessions are ephemeral** (workflow state lives in Redis/files/MLflow outside the loop); loops print structured traces and hook MLflow/OTel when configured.

### Skill layering (`tools/lib.py` pattern)

In 101/201, `tools/lib.py` is the single source of truth for what an agent can do. It is exposed **twice**: in-process via `TOOL_HANDLERS` / `TOOL_SCHEMAS` (the agent registers them as tool-call handlers) and as stdio MCP servers (`*_mcp.py`, thin `@mcp.tool()` wrappers). Add or change a skill in `lib.py`; the MCP server only re-declares its signature and docstring. Skills are env-switched, not forked: e.g. telemetry reads bundled CSVs by default, or the Feast online store when `FEAST_ONLINE_URL` is set — same code path either way.

### Decision discipline (202/301/302)

The higher courses share one shape: **the model gives a number, the agent reasons, code enforces the rails.** A calibrated classic model (KServe, reached as a *tool*, never embedded) supplies the quant signal; a GenAI judge supplies the qual verdict as a full co-decider; a code arbiter merges them, and one non-negotiable floor (escalate threshold, `HARD_RISK_FLOOR`, `HARD_QOS_FLOOR_PCT`) overrides regardless of what the LLM says. Judge-vs-quant disagreements are recorded as audited overrides. Judge unreachable → quant gate alone, honestly recorded. Preserve this asymmetry when editing: an LLM may never relax a rail.

### Docs site and tapes (`docs/`, `shared/tapes/`)

`shared/tapes/*-tape-raw.json.gz` are sanitized live snapshots of the Rome and Venice clusters (K8s resources, pod logs, MLflow runs, registry). `docs/portal/build-tapes.py` slices a raw tape into per-course player tapes in `docs/portal/tapes/`, **parsing the steps directly out of each course's `MANUAL.md`** (`## Step N:` with `**Why**/**Do**/**Expect**`).

That coupling is the thing to remember: **editing a course `MANUAL.md` step changes its tape.** After such an edit, regenerate the tape with `build-tapes.py` and commit it, or `run.sh`'s final stage fails on drift. Never hand-edit a file in `docs/portal/tapes/`. Everything a student sees in the portal is captured, never synthesized — see `shared/tapes/TAPE-SCHEMA.md`.

## Conventions

- **Env config, one contract.** `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` (201 also `LLM_MODEL_SMALL` / `LLM_MODEL_LARGE` for tier routing). Optional: `LLM_WIRE_LOG` (append every model HTTP request/response as JSONL, `Authorization` redacted — this is how QA wire evidence is produced), `LLM_CA_BUNDLE` (private CA PEM), `MLFLOW_TRACKING_URI` / `MLFLOW_TRACKING_TOKEN` / `MLFLOW_WORKSPACE`, `STATE_STORE_URL` (301). All courses share one `llm-credentials` Secret in-cluster (`shared/manifests/ocp/secret-llm.example.yaml`).
- **Never commit credentials or a filled `.env`.** `keys/`, `.env` are gitignored; the tapes were scrubbed before commit and must stay that way (cluster hostnames are deliberately kept).
- **Course folders are uniform:** `agent(s)/ tools/ deploy/ QA/ images/` plus `README.md` (what it teaches) and `MANUAL.md` (numbered Why/Do/Expect steps). Read 101 first; the others follow it.
- **OpenShift-safe code.** Containers run as a random UID under restricted PSS — write only to env-configurable paths (e.g. `REPORT_DIR`), never to the repo dir, in anything that ships in `deploy/`.
- **Optional deps import lazily** (`openai`, `mlflow`, `langchain-openai`) so `--offline` runs with a minimal install. Keep it that way.
