# Agent School — Roadmap & Stack Alignment

This document maps Agent School against Table 1 of our article *Agentic AI Stack Insideout* — the canonical taxonomy of solution components, roles, and implementation options — and lays out where the curriculum goes next. It is the honest ledger: what the courses already exercise, what they only gesture at, and what is not yet touched. No row is claimed as covered unless code in the repo exercises it.

## Coverage against the article's Table 1

| # | Component (role) | Status | Where in the repo |
|---|------------------|--------|-------------------|
| 1 | Agent blueprint / GitOps *(declare, deploy, version)* | Partial | Kustomize base + `rome/` overlays in every `deploy/`; no Helm/ArgoCD GitOps versioning as a lesson |
| 2 | Agent orchestration *(multi-agent coordination, discovery)* | Covered | 301 runs five agents over A2A with AgentCards; AGNTCY Agent-Exchange not touched |
| 3 | Sandbox runtime / supervisor *(constrain files, network, syscalls)* | Skipped | restricted-PSS `securityContext` is the closest; no Kata / K8s agent-sandbox |
| 4 | Harness *(loop, memory, context, tool use)* | Covered | custom loop (101), two-phase loop (201), LangGraph (202, 301), OGX (302), plus the OpenClaw product-harness track (101) |
| 5 | Agentic loop runtime / Agent-as-a-Service *(server-side loop, trace store)* | Partial | 302 uses OGX (Llama Stack); "server-side loop" is never the explicit lesson |
| 6 | Model *(reasoning service)* | Covered | vLLM serving Kimi and others on RHOAI, consumed by every course |
| 7 | AI gateway / MaaS *(auth, quota, OpenAI API)* | Partial | the RHOAI MaaS endpoint is consumed; not taught as a component |
| 8 | Semantic routing *(pick best price/performant model)* | Skipped as infra | 201 teaches an agent-side small-vs-large heuristic, not the vLLM Semantic Router |
| 9 | Inference routing *(pick replica: KV cache, load)* | Skipped | no llm-d Router / EPP |
| 10 | Model workers *(token generation on accelerators)* | Covered (implicit) | vLLM on the cluster serves every model |
| 11 | Skills *(domain capabilities as tools)* | Covered | MCP tool servers per course (telemetry, runbooks, retrieval, scorer, playbook, thinktank) |
| 12 | Workload identity *(cryptographic agent identity)* | Skipped | asserted in Conventions #3; 301 uses per-agent ServiceAccounts but no SPIFFE/SPIRE SVID |
| 13 | Tool governance *(authorize tool calls by token claims)* | Covered, strong | Kuadrant AuthPolicy + RateLimitPolicy in front of the MCP gateways in 301 and 302 |

**Scoreline:** covered 6, strong 1, partial 3, skipped 3. The harness, model, skills, and tool-governance rows are the repo's spine. The gaps cluster into two themes that line up with the platform's two headline promises.

## The gaps, grouped

**Price / performance — the model-serving intelligence tier.** Rows 7, 8, 9. Today a course picks a model and calls it; nothing in the repo routes a query to the cheapest capable model (semantic routing) or picks the warmest replica (inference routing). 201's "small-vs-large routing" is an agent-side heuristic, not the infrastructure component the article names. Closing this is the most direct expression of the price/performance argument.

**Security — the constrain-and-identify tier.** Rows 3, 12, and part of 1. The Conventions section already promises "one agent, one identity" and "the sandbox decides what the agent may touch" — but no course gives an agent a cryptographic SVID, runs it under a sandbox supervisor, or versions it through GitOps. This is the biggest integrity gap: the repo asserts these properties without demonstrating them.

## Roadmap — candidate courses

Each is scoped to close specific Table 1 rows. Numbering follows the existing grading scheme (1xx laptop-minutes, 2xx skill backends + routing, 3xx cluster closed loop), which is why the security and routing work land above 1xx. Status is **proposed** until a build is greenlit.

### 102 — NOC Assistant, Served *(proposed, 1xx)*
Closes row 5 (and touches 7). Reuses 101's exact skills but drives them through a **server-side loop runtime** (OGX / Responses API) instead of the client-side loop 101 hand-writes. Completes a clean arc — *the same skills, three ways to drive the loop*: client loop (101 `agent/`), product harness (101 OpenClaw track), server runtime (102). Laptop-runnable, smallest scope, no new infrastructure.

### 203 — Routing Done Right *(proposed, 2xx)*
Closes rows 7, 8, 9 — the **price/performance** theme. Replaces 201's agent-side heuristic with the real components: a vLLM Semantic Router that picks the cheapest capable model per query, llm-d / EPP inference routing that picks the warmest replica, behind a MaaS gateway. The lesson is measured cost and latency deltas, not a hand-wave.

### 303 — Constrained & Identified *(proposed, 3xx)*
Closes rows 3, 12, and deepens 13 — the **security** theme, and the biggest integrity gap. Gives an agent a SPIFFE/SPIRE SVID, runs it under a sandbox supervisor (Kata or K8s agent-sandbox) that constrains files, network, and syscalls, and has the MCP gateway authorize tool calls by that cryptographic identity. Turns Conventions #1–3 from assertions into a demonstrated, auditable property.

## Housekeeping note

The names NemoClaw and OpenShell appear in `101-noc-assistant/harness-tracks/openclaw.md` as "the 301 track." They trace to Table 1's blueprint/sandbox rows, where the article lists them as *(planned)* options — so they are forward-references, not invented stacks. The only correction owed is tense: they are planned article options, not an implemented 301 track. A one-line edit when 303 is scoped will settle it.

---

*This roadmap is descriptive of intent, not a delivery commitment. Course statuses in the top-level [README](../README.md) remain the source of truth for what is built and QA-passed today.*
