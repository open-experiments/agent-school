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

## The skill backends, live on Rome

Build stage 1 is live — both heavy skills exist for real before any
agent code, because simulate-before-act starts with having something
real to simulate against and score with.

**Pattern 2 — the sustainability scorer on KServe.** Trained
in-cluster ([training/train_sustainability.py](./training/train_sustainability.py))
as a faithful reproduction of the source notebook's recipe:
StandardScaler + LinearRegression over 11 network KPIs from the
experiment's published 100K-row 5G netops dataset, energy efficiency
= 100 − predicted fault rate (the source's own definition). r² 0.878
on the 20% holdout. Registered as `sustainability-energy-efficiency`
in MLflow and promoted to `rome-registry`, staged to MinIO, served by
the same MLServer-on-stock-UBI9 pattern 202 proved out — live V2
scoring verified against real dataset rows (served fault-rate
predictions track ground truth):

![Scorer deployment](./images/rhoai/scorer-deployment.png)

**Pattern 3 — the cell-sleep simulation as a queued Job.** The
simulation ([sim/cell_sleep_sim.py](./sim/cell_sleep_sim.py)) vendors
the airan-energy experiment's power and cost model (1000/700/500 W
active by load, 100 W light sleep, 200 W × 2 min wake transitions,
$0.12/kWh, 0.5 kg CO₂/kWh) with its diurnal traffic shape, vectorized
in JAX for a 24h sweep at 15-minute steps. QoS is physics, not
prompting: sleeping cells' traffic re-homes to awake neighbors' spare
capacity and the unservable remainder is reported as dropped. Each
proposal is one Kueue-admitted Job (queue label → shared ClusterQueue
→ Workload metrics), and the agent's ServiceAccount can do exactly
one thing: submit and poll these Jobs
([deploy/ocp/rome/sim-rbac.yaml](./deploy/ocp/rome/sim-rbac.yaml) —
the pattern-3 RBAC lesson). First live run through the queue:
night windows on 2 of 6 cells → 4.27% energy saved, 0.0% QoS drop,
logged to MLflow experiment `302-energy-optimizer` as `sim-manual`.

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

In progress — build stage 1 of 2 complete.

1. **Skill backends (done, live on Rome):** the sustainability scorer
   trained from the published dataset, registered, promoted to
   `rome-registry`, and serving on KServe (pattern 2, verified with a
   live V2 call); the JAX cell-sleep simulation running as
   Kueue-admitted Jobs under submit/poll-only RBAC (pattern 3,
   verified through the queue with MLflow evidence).
2. **Agent:** the Llama Stack loop — propose, dispatch the sim Job,
   score via the served model, threshold-gate, emit the change-plan
   artifact (rejections logged too).

RHOAI snapshots land as each stage goes live — no mockups.
