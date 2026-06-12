# 301 · Closed-Loop NetOps

The flagship: Diagnostic → Planning → Execution → Validation agents running
the autonomous remediation loop over A2A, with Ansible playbooks as the
governed remediation arm. This is the Telco-AIX autonet experiment rebuilt
on the open agentic architecture, the modernization our Agentic Telco AI
article describes.

**Source experiments:** [autonet](https://github.com/open-experiments/Telco-AIX/tree/main/autonet)
(four-agent 5G operations loop: AMF/SMF/UPF monitoring, per-NF vector
stores, remediation playbooks) and
[agentic](https://github.com/open-experiments/Telco-AIX/tree/main/agentic)
(the original framework). Their custom ACP/MCP protocols are replaced with
A2A and MCP-standard tooling.

**Harness:** LangGraph per agent, A2A between agents (teaching). A NemoClaw
track is planned: the same four agents deployed as a NemoClaw blueprint into
OpenShell sandboxes, which exercises the full control band of the article's
Figure-1.

## Architecture

![301 Closed-Loop NetOps architecture](./images/architecture.png)

## Solution flow

1. **Diagnostic** consumes real autonet AMF/SMF/UPF telemetry and the
   vector stores, detects the incident, and publishes findings over A2A.
2. **Planning** turns findings into a remediation plan with impact and
   resource estimates.
3. **Execution** actuates the plan: the real autonet playbooks (scale AMF,
   restart SMF, rebalance UPF) run behind the MCP Gateway under scoped
   RBAC; an agent that can run playbooks is the most dangerous component
   in the system, so this is where governance concentrates.
4. **Validation** verifies KPIs against the live series, triggers rollback
   on failure, and closes the cycle.
5. Workflow state lives outside every agent. Each agent is an ephemeral
   one-pod worker; any stage can die and be retried by a fresh session
   (the 12-Factor Agent discipline: strict hierarchy, no peer chatter,
   externalized state).

## Blueprint mapping

| Blueprint component | Here |
|---------------------|------|
| Harness | LangGraph per agent, one sandboxed pod each |
| Orchestration | A2A messaging + AgentCards |
| Tool governance | playbooks behind MCP Gateway, scoped RBAC |
| Externalized state | workflow store outside all agents |
| Product-harness track | NemoClaw blueprint + OpenShell (planned) |

## What it teaches

1. Multi-agent coordination without east-west chatter: a strict
   Diagnostic → Planning → Execution → Validation hierarchy.
2. Ephemeral workers and externalized state as the scaling unlock.
3. Governed actuation: the loop can touch the network only through an
   authorized, audited gateway path, with rollback owned by Validation.

## Status

Planned. Reuses 101 telemetry tools, the autonet playbook set, and the
autonet per-NF vector stores. Build order: state store and A2A skeleton,
then agents one by one, Validation last.
