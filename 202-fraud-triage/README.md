# 202 · Fraud Triage

A decision agent that consumes a trained fraud model as a tool, gathers
billing context, and routes each case to clear, hold, or escalate, with a
human-approval gate on the escalate path. The model scores; the agent
reasons; the human approves.

**Source experiment:** [revenueassurance](https://github.com/open-experiments/Telco-AIX/tree/main/revenueassurance)
(Balanced Random Forest and Transformer fraud models with the telecom
billing dataset published on Hugging Face).

**Harness:** LangGraph; its `interrupt()` is the cleanest way to teach the
human-approval pattern.

## Architecture

![202 Fraud Triage architecture](./images/architecture.png)

## Solution flow

1. **score**: the graph's first node calls the fraud model as a tool. The
   revenueassurance model is served on KServe (pattern 2); the agent never
   embeds it.
2. **context**: for non-trivial scores, the agent pulls the customer's
   billing records to ground the decision.
3. **decide**: clear and hold cases complete autonomously with a case note.
4. **approval gate**: the escalate path hits `interrupt()`; the graph
   checkpoints, pauses, and resumes only when a human approves. This is the
   pattern our blueprint's Maturity section lists as an open product item,
   implemented at the harness level today.
5. Every outcome writes an externalized audit record: model score, context
   gathered, decision, and approver identity.

## Blueprint mapping

| Blueprint component | Here |
|---------------------|------|
| Harness | LangGraph state machine in the pod |
| Skill backend (pattern 2) | fraud model on KServe (RHOAI) |
| Human-in-the-loop | LangGraph interrupt + checkpoint |
| Ephemeral sessions | graph state checkpointed externally |
| Audit | case record per decision |

## What it teaches

1. Classic ML as an agent tool: the score is an input to reasoning, never
   the decision itself on high-risk paths.
2. Human approval as a first-class graph state, not a bolt-on.
3. Audit records that make every decision reconstructable.

## Status

Planned. Requires serving the revenueassurance model (KServe or a local
FastAPI wrapper for laptop dev) and the HF billing dataset.
