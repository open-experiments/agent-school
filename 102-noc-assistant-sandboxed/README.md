# 102 · NOC Assistant, Sandboxed

> **Status: authored, not yet demonstrated.** Every claim below is written
> against the OpenShell and NemoClaw documentation, not against a captured
> run. Nothing here is QA-passed and the course does not appear on the site
> or in the interactive labs until [QA/](./QA/) holds real evidence. See
> [Why this status line exists](#why-this-status-line-exists).

The same NOC assistant as [101](../101-noc-assistant/), with the same skills,
answering the same question — inside a sandbox that decides what it may touch.
101 proves an agent can do the work. 102 proves the blast radius when it
misbehaves, and that is a different claim needing different evidence.

**Harness:** [OpenClaw](https://github.com/openclaw/openclaw) (product harness)
running under [NVIDIA OpenShell](https://github.com/NVIDIA/openshell) via
[NemoClaw](https://github.com/NVIDIA/NemoClaw). No agent code is written in
this course — that is the point. 101's `tools/` are reused verbatim.

## What it teaches

1. **The sandbox is the fourth component.** The blueprint's Table 1 names a
   sandbox runtime alongside the harness, the model, and the skills. Courses
   101–302 exercise three of the four; this is the missing one, and it was
   the roadmap's only row marked *Skipped*.
2. **Blast radius is declared, not promised.** [`openshell/policy.yaml`](./openshell/policy.yaml)
   states in YAML what the agent may read, reach, and call. A prompt asking
   an agent to behave is a wish; a Landlock ruleset is a mechanism.
3. **An agent that never holds its own credential.** NemoClaw's gateway
   rewrites the `Authorization` header at the network boundary, so
   `LLM_API_KEY` is absent from the sandbox. Every other course in this repo
   injects the key with `envFrom`. This one does not, and Convention #3
   stops being an assertion.
4. **The negative result is the deliverable.** Four labs, each ending in a
   refusal that is captured rather than described.

## Relationship to the other courses

| | 101 | 101 OpenClaw track | **102** |
|---|---|---|---|
| Harness | custom loop you read | product harness | product harness |
| Skills | MCP servers | same MCP servers | same MCP servers |
| Runs where | your laptop / a Job | your laptop | **an OpenShell sandbox** |
| Constrains files, network, syscalls | no | no | **yes, declaratively** |
| Holds its own API key | yes | yes | **no** |

Same skills, three ways to drive them — and the third one is the only one
where the platform, not the agent, decides what is reachable.

## The four labs

Each lab ends in a denial, and the denial is the artifact.

1. **Filesystem (Landlock).** The agent reads `data/` and `runbooks/` as
   declared, answers the NOC question, and is refused on `~/.ssh` and on any
   write outside `/sandbox`.
2. **Network (namespace + policy).** The model endpoint and the Feast online
   store are reachable; everything else is not. Egress to a host absent from
   the policy is refused at the boundary, not by the model declining.
3. **Hot reload.** Network policy tightens on a *running* sandbox, and the
   next tool call fails accordingly — the property that distinguishes a
   runtime from a container spec.
4. **Credential absence.** The sandbox's environment and filesystem are
   searched for `LLM_API_KEY`. The empty result, next to a successful
   inference call, is the evidence.

## Open question this course answers

**Does MCP-over-stdio survive the sandbox boundary intact?** The NemoClaw
architecture documents managed integrations and policy-gated external APIs
but does not state how stdio MCP servers are treated inside a sandbox. Every
course in this repo depends on that answer: if stdio MCP crosses cleanly, the
whole skill layer transfers unchanged; if it does not, the skills move behind
the gateway as network integrations and the blueprint's "tools go through
MCP" convention acquires a caveat worth teaching. Nobody in this repo has
measured it. The answer lands in `QA/`.

## Prerequisites

- A container runtime (Docker or Podman) on the host, or the Kubernetes
  driver for the in-cluster path.
- Linux, Apple Silicon macOS, or Windows/WSL2. Sandboxes need Landlock,
  seccomp, and network namespaces; on macOS these come from the container
  VM's kernel.
- An OpenAI-compatible endpoint, registered as an OpenShell *provider* rather
  than exported into the agent's environment.
- No GPU. As with every course here, the agent is a CPU-only workload.

## Why this status line exists

Convention 6 of this repository says claims carry evidence, and the tape
format's first design rule says everything a student sees was captured, never
generated. A course whose entire subject is *demonstrated* constraint would
fail its own premise if it shipped as documentation of a run nobody made.

What is blocking, honestly: on the Venice reference cluster both GPUs are
currently held by an unrelated benchmark, which leaves the shared
`kimi-linear-48b-a3b` endpoint `READY=False`, so a live agent run has no model
to reason with. The labs are written; the capture is scheduled. When it lands,
this section is replaced by a test matrix like [101's](../101-noc-assistant/QA/README.md),
the roadmap's row 3 moves off *Skipped*, and the course joins the site.
