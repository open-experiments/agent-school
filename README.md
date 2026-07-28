# Agent School

<p align="center">
  <img src="./shared/images/agentschool-logo.png" alt="Agent School logo" width="320"/>
</p>

Agentic AI solution implementation examples for telco, built to run on Red Hat OpenShift AI (RHOAI).

Each example takes a proven experiment from [Telco-AIX](https://github.com/open-experiments/Telco-AIX) and rebuilds it as a governed agent workload, following the solution architecture from our article *Agentic AI Stack Insideout*: the model is a stateless inference service, the harness owns the loop and the tool calls, and the sandbox runtime decides what the agent may touch.

## Quickstart

Two commands to a working agent, no model endpoint needed:

```bash
cd 101-noc-assistant && pip install -r requirements.txt
python3 agent/noc_agent.py --offline "What is wrong in the core right now?"
```

That replays a scripted agent episode over the real 5G core dataset, printing every plan step, tool call, and observation. When it makes sense, point the same agent at any OpenAI-compatible endpoint (`cp .env.example .env`, fill in `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`; endpoint options in [shared/manifests/vllm-rhoai.md](./shared/manifests/vllm-rhoai.md)) and run it live.

## Curriculum

| Course | Example | Status | Source experiment | Teaching harness (code) | What it teaches |
|--------|---------|--------|-------------------|-------------------------|-----------------|
| [101](./101-noc-assistant/) | NOC Assistant | **working, QA-passed** | 5gprod, telco-sme | custom loop (OpenAI-compatible) | Agent loop, MCP tools, vLLM serving, tracing |
| [201](./201-rca-investigator/) | RCA Investigator | **working, QA-passed** | llm-rca | custom two-phase loop (OpenAI-compatible) | RAG as a skill backend, small-vs-large model routing, evidence-grounding judge |
| [202](./202-fraud-triage/) | Fraud Triage | **working, live on Rome** | revenueassurance | LangGraph | Train-serve pipeline, classic ML model as a governed tool, human-approval gate |
| [301](./301-closed-loop-netops/) | Closed-Loop NetOps | **working, live-proven on Rome** | autonet, agentic | LangGraph + A2A | Multi-agent closed loop, governed actuation (Kuadrant), quant + qual co-decision on the plan |
| [302](./302-energy-optimizer/) | Energy Optimizer | **working, live on Rome** | airan-energy, sustainability | OGX (Llama Stack) | Simulate-before-act, quant + qual co-decision, LLM-as-a-judge measured |

Courses are graded: 1xx runs in minutes on a laptop against any OpenAI-compatible endpoint; 2xx adds skill backends, routing, and a train-serve pipeline; 3xx closes the loop on a cluster, where agents act under governance and a calibrated model co-decides with a GenAI judge.

Table-1 of the article distinguishes two kinds of harness, and the curriculum covers both. Teaching harnesses (the custom loops, LangGraph, and OGX) are libraries: you write the agent and the loop is visible, which is what every course above ships. A product harness is an always-on system you configure instead of code; 101 additionally ships an [OpenClaw track](./101-noc-assistant/harness-tracks/openclaw.md) that runs the same MCP skills under such a harness, with no agent code written. Every example's skills are MCP servers precisely so both kinds can drive identical tools; that is the harness-agnosticism of the blueprint, made testable.

## Conventions

These rules hold across every example, and they come straight from the blueprint:

1. **No GPU in the agent pod.** Agents are CPU-only loops; models are served by vLLM on RHOAI, and heavy skills run as separate services or batch jobs.
2. **Tools go through MCP.** Every capability an agent uses is exposed as an MCP tool, so tool authorization can move to a gateway without code changes.
3. **One agent, one process, one identity.** Examples are written so each agent can later run as one sandboxed pod with its own ServiceAccount or SPIFFE identity.
4. **Sessions are ephemeral.** Workflow state lives in files or stores outside the loop; any session can be restarted with a clean context (see our 12-Factor Agent article).
5. **Everything is traceable.** Agent loops print structured traces, and hook into MLflow/OpenTelemetry when configured.
6. **Claims carry evidence.** QA-passed courses (101, 201) ship a full `QA/` pack: deterministic offline runs, tool smoke tests, and live runs with wire-level request/response captures (auth redacted). The newer courses ship offline-runnable code and are being brought to the same bar. If a course says QA-passed, the logs are in the repo.

## Prerequisites

- Python 3.10+
- An OpenAI-compatible model endpoint for live runs: RHOAI Model-as-a-Service, a vLLM instance, local llama.cpp, or any hosted equivalent (see [shared/manifests/vllm-rhoai.md](./shared/manifests/vllm-rhoai.md)). Set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
- Each working example also ships an `--offline` mode that runs a scripted episode with no endpoint, so the loop mechanics can be studied first.

## Layout

Every course folder follows the same shape, so once you have read 101 you can navigate any of them:

```
agent-school/
├── 101-noc-assistant/        # working, QA-passed: agent + MCP tools + real 5gprod data + runbooks
│   ├── agent/                #   the teaching harness (readable tool-calling loop)
│   ├── tools/                #   skills: in-process lib + stdio MCP servers
│   ├── data/  runbooks/      #   real Telco-AIX dataset and matching runbooks
│   ├── feature_repo/         #   Feast feature views (train/serve parity)
│   ├── training/             #   model train + register (MLflow)
│   ├── harness-tracks/       #   same skills under a product harness (OpenClaw)
│   ├── deploy/               #   Containerfile + OpenShift manifests (Job/CronJob)
│   ├── QA/                   #   evidence pack: logs + wire traces
│   └── images/               #   architecture drawing + walkthrough video
├── 201-rca-investigator/     # working, QA-passed: two-phase RCA agent + RAG service backend
│   ├── agent/  tools/        #   as above
│   ├── backend/              #   pattern-2 skill backend (FastAPI RAG service)
│   ├── eval/                 #   evidence-grounding judge (LLM-as-a-judge)
│   ├── deploy/  QA/  images/ #   as above (Deployment + Service for the backend)
│   └── reports/              #   cited RCA artifacts from QA runs
├── 202-fraud-triage/         # working, live on Rome: pipeline + serving + triage agent
│   ├── agent/                #   LangGraph triage: score → context → decide → gate → audit
│   ├── pipeline/             #   DS Pipelines: train → register → promote
│   ├── serving/              #   KServe deployment (model as a tool)
│   └── deploy/  QA/  images/ #   as above
├── 301-closed-loop-netops/   # live-proven on Rome: loop + governed actuation + co-decision
│   ├── agents/               #   diagnose / plan / execute / validate + judge (over A2A)
│   ├── training/             #   remediation-risk scorer (the quant signal)
│   ├── serving/              #   KServe scorer + Kuadrant gateway policies
│   └── deploy/  QA/  images/ #   as above
├── 302-energy-optimizer/     # live on Rome: quant + qual co-decision, judge measured
│   ├── agent/  sim/          #   OGX optimizer + simulate-before-act
│   ├── training/  serving/   #   sustainability scorer on KServe, governed /score
│   ├── eval/                 #   the judge, scored (datasets + LLM-as-a-judge)
│   └── deploy/  QA/  images/ #   as above
└── shared/                   # endpoint options, OCP secret template
```

## Deploy on OpenShift

All five courses ship `deploy/` folders: UBI9 Containerfiles, ImageStream + BuildConfig for in-cluster builds, and restricted-PSS manifests with the right workload shapes (agents as Jobs/CronJobs, backends and scorers as Deployment/KServe + Service, and Kuadrant gateway policies for the governed courses). One `llm-credentials` Secret serves all courses ([shared/manifests/ocp/secret-llm.example.yaml](./shared/manifests/ocp/secret-llm.example.yaml)). Start at [101-noc-assistant/deploy/](./101-noc-assistant/deploy/).

## License

[MIT](./LICENSE).
