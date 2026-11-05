# Agent School — Roadmap & Stack Alignment

This document maps Agent School against Table 1 of our article [*Architect an open blueprint for cloud-native AI agents*](https://developers.redhat.com/articles/2026/07/20/architect-open-blueprint-cloud-native-ai-agents) — the canonical taxonomy of solution components, roles, and implementation options — and lays out where the curriculum goes next. It is the honest ledger: what the courses already exercise, what they only gesture at, and what is not yet touched. No row is claimed as covered unless code in the repo exercises it.

The working-group kickoff deck (November 2026) presents this plan: [PDF](roadmap/agent-school-roadmap-wg-kickoff-2026-11.pdf) · [PPTX](roadmap/agent-school-roadmap-wg-kickoff-2026-11.pptx) · [sources and build](roadmap/).

## Coverage against the article's Table 1

| # | Component (role) | Status | Where in the repo |
|---|------------------|--------|-------------------|
| 1 | Agent blueprint / GitOps *(declare, deploy, version)* | Partial | Kustomize base + `rome/` overlays in every `deploy/`; no Helm/ArgoCD GitOps versioning as a lesson |
| 2 | Agent orchestration *(multi-agent coordination, discovery)* | Covered | 301 runs five agents over A2A with AgentCards; AGNTCY Agent-Exchange not touched |
| 3 | Sandbox runtime / supervisor *(constrain files, network, syscalls)* | Skipped — 102 in build, unproven | restricted-PSS `securityContext` is the closest today; 102 authors an OpenShell policy but ships no captured denials yet, so the row stays Skipped |
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

**Platform layer: the cross-workload tier.** Row 11 in both of its halves (skills as procedural knowledge, capabilities as MCP), the knowledge placement rule, and the domain-model track (rows 6 and 7). None of the 1xx to 3xx courses can carry these without being rewritten, which is why they get a grade of their own: the 4xx series below.

## Roadmap — candidate courses

Each is scoped to close specific Table 1 rows. Numbering follows the grading scheme (1xx laptop-minutes, 2xx skill backends + routing, 3xx cluster closed loop, 4xx platform layer), which is why the security and routing work land above 1xx. Status is **proposed** until a build is greenlit.

Candidates are also debated in [GitHub Discussions](https://github.com/open-experiments/agent-school/discussions). Until November 2026 the threads carried a 4xx ballot alias per candidate; those aliases are **retired** now that 4xx is a grade of its own (the platform layer, below). The [retired alias table](#retired-ballot-aliases) at the end keeps the votes traceable.

### 102 — NOC Assistant, Sandboxed *(in build, 1xx)*
Closes row 3 — the only *Skipped* row — and takes a first bite of 12. Reuses 101's skills verbatim under the OpenClaw product harness, running inside an [NVIDIA OpenShell](https://github.com/NVIDIA/openshell) sandbox via [NemoClaw](https://github.com/NVIDIA/NemoClaw): filesystem confined by Landlock, egress by network policy, syscalls by seccomp, all declared in [`102-noc-assistant-sandboxed/openshell/policy.yaml`](../102-noc-assistant-sandboxed/openshell/policy.yaml) rather than promised in a prompt. NemoClaw's gateway rewrites `Authorization` at egress, so the agent never holds `LLM_API_KEY` — the first demonstration in this repo of Convention #3 rather than an assertion of it.

Authored August 2026; **not yet demonstrated**, and it does not move row 3 off *Skipped* until [its QA pack](../102-noc-assistant-sandboxed/QA/README.md) holds captured denials. It also answers an open question the whole curriculum rests on: whether MCP-over-stdio survives a sandbox boundary intact.

### 104 — NOC Assistant, Served *(proposed, 1xx)*
Closes row 5 (and touches 7). Reuses 101's exact skills but drives them through a **server-side loop runtime** (OGX / Responses API) instead of the client-side loop 101 hand-writes. Completes a clean arc — *the same skills, three ways to drive the loop*: client loop (101 `agent/`), product harness (101 OpenClaw track), server runtime (102). Laptop-runnable, smallest scope, no new infrastructure.

### 203 — Routing Done Right *(proposed, 2xx)*
Closes rows 7, 8, 9 — the **price/performance** theme. Replaces 201's agent-side heuristic with the real components: a vLLM Semantic Router that picks the cheapest capable model per query, llm-d / EPP inference routing that picks the warmest replica, behind a MaaS gateway. The lesson is measured cost and latency deltas, not a hand-wave.

[Discussion #22](https://github.com/open-experiments/agent-school/discussions/22) sharpens the scope and supersedes the sketch above: it is an **integration and measurement course, not a build** — compose vLLM Semantic Router (tier 1+2) with llm-d (tier 3), use RouteLLM as the evaluation method, and publish cost-per-resolved-episode over our own tapes. The original contribution is that every published routing number is for chat traffic, while agent loops route per *step* and re-send growing context, so prefix-cache affinity should pay off more. Treat the thread as the live scope.

### 303 — Constrained & Identified *(proposed, 3xx)*
Closes rows 3, 12, and deepens 13 — the **security** theme, and the biggest integrity gap. Gives an agent a SPIFFE/SPIRE SVID, runs it under a sandbox supervisor (OpenShell, Kata, or Kubernetes `agent-sandbox`) that constrains files, network, and syscalls, and has the MCP gateway authorize tool calls by that cryptographic identity. Turns Conventions #1–3 from assertions into a demonstrated, auditable property.

**Both halves are in scope, and the ballot dropped one.** The option for this course in the [roadmap poll](https://github.com/open-experiments/agent-school/discussions/23) (listed there as "403 Workload identity", an alias since retired) reads as SPIFFE/SPIRE only; the supervisor is the row-3 half and the reason this item exists — an identity with no sandbox is a credential an unconstrained process can leak, and a sandbox with no identity is a box that cannot tell the gateway who is inside it. The supervisor choice is deliberately open: OpenShell is a *(planned)* option named in the article's Table 1, not a decision already made, and it competes with Kata (VM isolation, heaviest tax) and Kubernetes `agent-sandbox`. Evidence bar: this course fails its own premise if it ships configuration instead of captured denials — blocked egress, blocked write, blocked syscall — in a `QA/` pack.

### 304 — Agent SecOps *(proposed, 3xx)*
Adversarial counterpart to 303, scoped in [discussion #20](https://github.com/open-experiments/agent-school/discussions/20): prompt-injected tool escalation against the MCP gateway's authz, RAG poisoning against 201's grounding judge, approval-gate social engineering against 301's risk scorer, and memory/session poisoning against convention 4 — all shipped as replayable attack tapes with attack-success-rate before and after each mitigation. Sources the unused `secops` / `iot-sec` Telco-AIX experiments.

Sequencing note: 303 before 304. A sandbox's value is only ever provable adversarially, so 303 gives 304 its most interesting target, and "escape attempt blocked" is an attack tape by construction.

### 103 — Telco-SME Assistant *(proposed, 1xx)*
Rebuilds the Telco-AIX SME assistant as a governed agent workload with MCP skills and a QA pack. Closes no new Table 1 row — it widens the domain surface at the 101 shape, which is why it grades 1xx.

## 4xx: the platform layer (new grade, November 2026)

1xx through 3xx each build one agent workload. A 4xx course builds one cross-workload platform component and proves it against the existing 1xx to 3xx courses as consumers, **without modifying them**: their tapes, walkthroughs and QA packs stay as shipped, and the deviations the new component exposes in them are recorded in the syllabus rather than patched. This is the positioning doc's squid made into courses: the tentacles get their own numbers.

Two vocabulary rules come with the grade. A **skill** is procedural knowledge bound to a role (how the job gets done). A **capability** is what an agent reaches outside its own process through MCP: context in on the read side, actions out on the write side. A2A is role delegation. 4xx courses use the words that way; the shipped courses keep their original wording ("skills" for what the grade calls capabilities), reconciled by a glossary in the syllabus, where the article's Table 1 row 11 splits into Skills and Capabilities.

### 401: Skills and capabilities *(proposed, 4xx)*
A skill catalog (one SKILL.md per role, governed as content: review, version, provenance, a judge), a capability catalog (a manifest per MCP server: backend pattern 1/2/3, I/O schema, risk class, allowed identities, rate class, data class), a registry generator that emits the inventory, the per-agent allow-lists and the Kuadrant AuthPolicy and RateLimit objects (CI-checked byte-identical, like the tapes), and selection in two layers: procedure per problem class, capability per step, both recorded in the trace. Proven with 101, 301 and 302 as consumers. Closes the Skills half of row 11, deepens row 13 (per-tool authorization) and moves row 1 toward covered (policies declared and versioned from one source). Evidence bar: an offline mode for the generator and the selection drill, and a QA pack at the 101 bar.

Capabilities come in three provider tiers, recorded in the manifest: **platform** (the MCP backends OpenShift ships for platform operations, and the OpenShift AI MCP catalog and gateway), **telco application layer** (CNF vendor backends, onboarding in progress), and **course-local** (the think-tank, scorer and playbook servers the shipped courses run). The registry generator emits what the platform consumes, so the Kuadrant policies today and the gateway routing as it matures come from one source. A vendor backend joins the registry by publishing a capability manifest and passing one offline conformance episode (a tape with a golden trace); the course runs on the episode, and vendor lab access is optional. The 401 lab swaps 301's hand-rolled playbook server for the OpenShift MCP backend and one fiveg-core stand-in for a CNF vendor backend behind the same policies, then diffs the traces; 301 itself stays as shipped.

### 402: Knowledge *(proposed, 4xx)*
The RAG placement rule as a platform service: retrieval holds knowledge the domain owns (runbooks, specs, past RCAs, alarm dictionaries), structured state comes through tools, and a procedure the role owns is a skill. Builds a knowledge service beside 201 (compared with its KPI-rows-as-text corpus, which stays as shipped), retrieval over the 401 inventory so selection scales past a flat tool list, case memory over 301's loop episodes and gate decisions (the data issue #19 needs), and topology retrieval for the blast-radius context the judge lacks. Platform choice open: hand-rolled FastAPI + TF-IDF or Llama Stack vector_io. Closes the knowledge half of row 11; touches row 5.

### 403: Domain model track *(proposed, 4xx)*
AT&T's OTEL 2.0 (Gemma 4 31B-IT post-trained with OSFT on about 440B telecom tokens, Apache-2.0) in the model slot every course already treats as a stateless endpoint. First, measure on the tapes what the model card leaves open: tool-calling (101 and 201 wire logs), judge groundedness (the 302 suite), closed-loop behavior (301), and cost per resolved episode as the small tier (201 routing, 203). Then the tuning loop: SDG Hub turns the 401 catalogs and the tapes into the telecom tool-calling and MCP data the card says its mix lacks, and OSFT folds it in without eroding what the model already knows. Rows 6 and 7; feeds 203's small tier. The conformance episodes of the CNF vendor backends onboarded through 401 join those tapes as SDG Hub input: tool-calling against real telco application backends, which no shipped course has.

## Pipeline candidates (not courses)

The discussions also nominate work on the machinery around the curriculum rather than new courses. Tracked here so the roadmap is the whole ledger:

- **CI evidence gate** ([#21](https://github.com/open-experiments/agent-school/discussions/21)) — replay every course's `--offline` episode on each PR, diff golden traces *structurally* (tool-call sequence, argument schema, verdicts) so prompt wording can change but behavior cannot, and publish the regenerated `QA/` pack as a build artifact. Upgrades convention 6 from a snapshot claim to a standing guarantee, and is the prerequisite for accepting outside contributions without eroding the evidence bar. Small effort: the hard part (deterministic offline episodes) is already a repo convention.
- **One-command OpenShift deploy**, **shared MCP tool registry**, **scheduled tape refresh** ([#24](https://github.com/open-experiments/agent-school/discussions/24)) — the other three on the hardening ballot.

## Next build item (committed): the 202 triage agent — DONE (July 2026)

**Delivered.** The agent is live on Rome: LangGraph score → context → decide → gate → audit, `fraud-detector` consumed as a KServe tool, the approval gate (`interrupt()`) proven in both directions on the cluster, cases audited to the `revassurance-fraud` experiment, offline mode + QA pack in the repo, and the root README's 202 row now reads working, live on Rome. Original scope kept below for the record.

The one real build gap inside the existing curriculum, surfaced by the July 2026 status audit: 202's model factory and serving are live on Rome, but the LangGraph triage agent itself (score, context, decide, with the human-approval gate) exists in the architecture and walkthrough, not yet as code in the repo. Building it closes the course: the agent consumes `fraud-detector` on KServe as a tool, grounds case context in the Feast billing features, enforces the approval gate in code on the escalate path, audits every case to MLflow, ships an `--offline` mode, and fills the `QA/` pack to the 101 bar. When it lands, the root README's 202 row flips from "model factory live on Rome; agent next" to working, and 202 becomes the fifth fully-closed course. This item precedes the proposed courses below.

## Retired ballot aliases

Until November 2026 the discussion threads labelled candidates with a 4xx ballot alias. Those labels are retired because 4xx is now the platform-layer grade and 401 to 403 name real courses above. Votes already cast stay traceable through this table, and the threads carry a retirement note.

| Retired alias (discussion) | Canonical course | Closes Table 1 rows | Status |
|---|---|---|---|
| 401 Agent SecOps ([#20](https://github.com/open-experiments/agent-school/discussions/20)) | **304** | none new; adversarial evidence for 3, 12, 13 | proposed |
| 402 Route the loop ([#22](https://github.com/open-experiments/agent-school/discussions/22)) | **203** | 7, 8, 9 | proposed |
| 403 Workload identity ([#23](https://github.com/open-experiments/agent-school/discussions/23)) | **303** | 3, 12; deepens 13 | proposed |
| 404 Telco-SME rebuild ([#23](https://github.com/open-experiments/agent-school/discussions/23)) | **103** | none new | proposed |
| no thread yet | **104** | 5; touches 7 | proposed |
| no thread yet | **102** | 3; touches 12 | in build, unproven |

New 4xx course threads open under their real numbers (401, 402, 403), so the old labels do not collide: a 4xx number in a thread opened after this note names a course.

## Housekeeping note — SETTLED (August 2026)

The names NemoClaw and OpenShell appear in `101-noc-assistant/harness-tracks/openclaw.md` and in 301's blueprint table. They trace to Table 1's blueprint/sandbox rows, where the article lists them as *(planned)* options — so they are forward-references, not invented stacks. The only correction owed was tense: `openclaw.md` described them as "the 301 track" in the present tense, when they are planned article options. **Fixed August 2026**; both mentions now read as planned. The substantive work they point at is unbuilt and lives in 303 above — and note that OpenShell answers the *sandbox supervisor* half of row 3, which is a separate concern from the NemoClaw *product-harness* packaging track 301 sketches.

---

*This roadmap is descriptive of intent, not a delivery commitment. Course statuses in the top-level [README](../README.md) remain the source of truth for what is built and QA-passed today.*
