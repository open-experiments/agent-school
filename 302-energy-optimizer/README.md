# 302 · Energy Optimizer

An agent that proposes RAN cell-sleep windows, simulates them before acting,
and emits a change plan only when the simulated outcome clears an
efficiency threshold. Simulate-before-act as a first-class pattern.

**Source experiments:** [airan-energy](https://github.com/open-experiments/Telco-AIX/tree/main/airan-energy)
(JAX-based DQN cell-sleep optimization, reporting 30-35% RAN energy savings)
and [sustainability](https://github.com/open-experiments/Telco-AIX/tree/main/sustainability)
(energy-efficiency prediction model published at
[fenar/sustainability](https://huggingface.co/fenar/sustainability)).

**Harness:** Llama Stack.

## Architecture

![302 Energy Optimizer architecture](./images/architecture.png)

The zones make the safety argument visible. The agent pod holds only the
Llama Stack loop — propose, dispatch, score, threshold gate. The two
heavy skills live in the cluster under different patterns: the JAX DQN
simulation as a batch Job the agent may only submit and poll (pattern 3,
the RBAC lesson), and the sustainability scorer served on KServe
(pattern 2). Everything the loop produces — proposals, simulations,
scores, rejections — lands in MLflow. And the RAN itself is firmly in the
external zone: the agent's only output is a change-plan artifact with the
simulation and score attached; it never touches the network.

## Solution flow

1. The agent reasons over traffic forecasts and proposes candidate
   cell-sleep windows.
2. It submits the airan-energy JAX simulation as a batch Job (pattern 3:
   K8s Job API, scoped RBAC) and polls for results; the GPU work never runs
   in the agent pod.
3. The simulated plan is scored by the sustainability model served on
   KServe (pattern 2).
4. Score at or above threshold: the agent emits the change plan artifact
   with the simulation evidence attached. Below threshold: it revises the
   proposal and loops.

## Blueprint mapping

| Blueprint component | Here |
|---------------------|------|
| Harness | Llama Stack loop in the pod |
| Skill backend (pattern 3) | JAX DQN simulation as batch Job |
| Skill backend (pattern 2) | sustainability scorer on KServe |
| Decision discipline | no plan without simulation + score attached |
| Audit | proposal, simulation, score, decision all recorded in MLflow — rejected proposals included |

## What it teaches

1. Pattern 3 in practice: job dispatch, polling, and the RBAC scrutiny it
   demands.
2. Simulate-before-act: the agent's own loop enforces evidence.
3. KPI-bound self-rejection: the threshold lives in the loop, not in the
   prompt.

## Status

Planned. Requires the airan-energy JAX environment packaged as a Job image
and the sustainability model served (KServe, or a local wrapper for laptop
dev). The MLflow evidence trail follows the tracing pattern 101/201
verified live on Rome. RHOAI snapshots will be added as stages go live —
no mockups.
