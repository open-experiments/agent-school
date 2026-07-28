# Course 301 Execution Manual: Closed-Loop NetOps

A step-by-step guide for the largest course: an autonomous Diagnostic, Planning, Execution, Validation loop over a stand-in 5G core, with externalized state (Redis), a governed MCP tool boundary (Kuadrant AuthPolicies on a dedicated Gateway), an external reasoning service in its own trust domain, and a quant+qual co-decision (risk model + LLM judge) in front of every plan.

Every step has three parts: **Why** explains what the step is for and what it means in the architecture, **Do** gives the exact action, and **Expect** gives the success signal. This is the course where the platform pieces meet. Budget a half day the first time.

## Just look at it (no deployment needed)

| What | Where |
|---|---|
| The five agents + MCP servers | Console (`rome` or `venice`), project `agent-school`: Deployments `diagnostic-agent`, `planning-agent`, `execution-agent`, `validation-agent`, `plan-judge-agent`, `mcp-playbook`, `risk-scorer-mcp`, `loop-state`, `llama-stack` |
| The actuation target | Project `fiveg-core`: Deployments `amf`, `smf`, `upf` |
| The external reasoner | Project `think-tank`: Deployment `think-tank` |
| Gateway governance | Project `agent-school`, resources AuthPolicy `mcp-playbook-authn` and `plan-scorer-authn` (both Accepted + Enforced), RateLimitPolicies, HTTPRoutes on Gateway `netops-gateway` |
| The risk model | AI hub, Models, Deployments `netops-remediation-risk`; Registry `netops-remediation-risk` |
| Loop episodes and co-decision params | Experiments, `301-closed-loop` |

## What you will build

Twelve-Factor agents: every agent holds zero local state and reads/writes loop records in a Redis state store, so any worker can die mid-loop. Execution's only path to the network is MCP playbook tools behind a Kuadrant-governed Gateway that authenticates ServiceAccount tokens and authorizes exactly one caller. Planning consults an external think-tank (separate namespace, separate credentials, nothing shared) and clears every plan through a quantitative risk scorer (GradientBoosting, served on KServe, reached only through the governed `/plan-score` route) plus an LLM plan-judge. Consensus lets a plan run autonomously; disagreement is audited.

The big idea of 301 is that **autonomy is earned through governance**: every capability an agent gains (touching the network, scoring a plan, deciding alone) is matched by an identity check, a policy, or a second opinion that is enforced by the platform, not promised by the prompt.

## Prerequisites (verify before you start)

1. Courses 101 and 202 environments in place: project `agent-school` with `llm-credentials`, `mlflow-tracking`, `feature-store-client`, the SA-group MLflow binding, `dspa-minio-creds`, and the `fraud-serving` SA + `aws-connection-minio-models` data connection (202 Step 6; the 301 serving reuses both).
2. Kimi-Linear Ready in `telco-aix`; managed MLflow with server-side job execution on; MinIO with the `models` bucket.
3. The Gateway class `data-science-gateway-class` exists (RHOAI aigateway component). The platform's shared gateway only accepts routes from its own namespaces, so this course creates its own Gateway; on bare-metal SNO it will show `AddressNotAssigned`, which is fine because all calls are east-west through the ClusterIP.
4. Feast `fivegprod` loaded (101): Diagnostic reads anomaly verdicts from the online store.

## Step 1: Namespaces and secrets

**Why:** the namespaces ARE the architecture diagram. `agent-school` holds the agents, `fiveg-core` is the actuation target (the "network" the loop is allowed to touch, and the only thing it can touch), and `think-tank` models an external reasoning provider: separate namespace, separate ServiceAccounts, separate secrets, no shared objects. The think-tank gets its own copy of the LLM credentials even though the values are identical, because trust domains that share Secret objects are not separate trust domains. `loop-state-auth` is generated fresh per cluster; state-store credentials are infrastructure, never repo content.

**Do (Console):** Administration, Namespaces: create `fiveg-core` (label `app.kubernetes.io/part-of=agent-school-target`) and `think-tank` (label `app.kubernetes.io/part-of=agent-school-external`), if 101 did not already create them.

```
oc create secret generic loop-state-auth -n agent-school \
  --from-literal=password="$(openssl rand -hex 16)"
oc create secret generic llm-credentials -n think-tank \
  --from-literal=LLM_BASE_URL=http://kimi-linear-48b-a3b-predictor.telco-aix.svc.cluster.local:8080/v1 \
  --from-literal=LLM_API_KEY=none \
  --from-literal=LLM_MODEL=kimi-linear-48b-a3b
```

**Expect:** both namespaces exist with their labels; both secrets exist.

## Step 2: Source ConfigMaps (all of them, once)

**Why:** every agent and MCP server in this course runs on stock UBI9 Python and mounts its source from a ConfigMap, pip-installing its dependencies at startup. That trades a slower first boot for a property that matters in a course (and in regulated shops): there is no custom image to build, sign, mirror, or trust; what runs is exactly the text you can read in the repo and in the ConfigMap. The `nf-playbooks` ConfigMap is special: it holds the four audited Ansible playbooks that are the ONLY actions Execution can ever take. Changing what the loop is allowed to do to the network means changing this ConfigMap, which is a reviewable, auditable event.

**Do:** from a repo checkout, inside `301-closed-loop-netops/`:

```
oc create configmap diagnostic-agent-src -n agent-school --from-file=agent.py=agents/diagnostic/agent.py
oc create configmap planning-agent-src   -n agent-school --from-file=agent.py=agents/planning/agent.py
oc create configmap validation-agent-src -n agent-school --from-file=agent.py=agents/validation/agent.py
oc create configmap execution-agent-src  -n agent-school --from-file=agent.py=agents/execution/agent.py
oc create configmap plan-judge-agent-src -n agent-school --from-file=agent.py=agents/judge/agent.py
oc create configmap risk-scorer-mcp-src  -n agent-school --from-file=server.py=agents/scorer-mcp/server.py
oc create configmap mcp-playbook-src     -n agent-school --from-file=server.py=agents/mcp-playbook/server.py
oc create configmap think-tank-src       -n think-tank   --from-file=server.py=agents/thinktank/server.py
oc create configmap nf-playbooks         -n agent-school \
  --from-file=agents/execution/playbooks/scale_amf.yml \
  --from-file=agents/execution/playbooks/restart_smf.yml \
  --from-file=agents/execution/playbooks/rebalance_upf.yml \
  --from-file=agents/execution/playbooks/rollback.yml
oc create configmap chain-smoke-src      -n agent-school --from-file=smoke_chain.py=agents/planning/smoke_chain.py
oc create configmap diagnostic-smoke-src -n agent-school --from-file=smoke_client.py=agents/diagnostic/smoke_client.py
oc create configmap loop-smoke-src       -n agent-school --from-file=smoke_loop.py=agents/execution/smoke_loop.py
oc create configmap validate-smoke-src   -n agent-school --from-file=smoke_validate.py=agents/validation/smoke_validate.py
oc create configmap remediation-train-src -n agent-school --from-file=train.py=training/train_remediation_risk.py
oc create configmap remediation-stage-src -n agent-school --from-file=stage_model.py=serving/stage_model.py
oc create configmap llama-stack-config   -n agent-school --from-file=run.yaml=../302-energy-optimizer/agent/run.yaml
```

**Expect:** sixteen ConfigMaps created without error.

## Step 3: Infrastructure tier

**Why:** each file in this tier is one architectural commitment.
- `state-store.yaml` (Redis): the loop's externalized memory. Every agent writes its stage record here (`loop:<id>:diagnostic`, `loop:<id>:status`, ...) and holds nothing locally, so any agent pod can die mid-loop and a replacement continues. This is 12-factor discipline applied to agents.
- `fiveg-core.yaml`: the stand-in AMF/SMF/UPF Deployments plus the `nf-actuator` Role scoped to exactly that namespace. The NF payloads are honest placeholders; the mechanics (scoped identity, real scale and rollout actions, observable state, rollback) are fully real.
- `execution-rbac.yaml`: binds Execution's identity to what it may touch. Note what is missing: no cluster roles, no wildcard verbs.
- `think-tank.yaml`: the external reasoner as an MCP server over streamable HTTP. Planning knows only its URL; the wire contract would be identical if this ran off-cluster, which is the point of the boundary.
- `netops-gateway.yaml` + `mcp-playbook.yaml` + `mcp-gateway-policies.yaml`: the governed tool path. The playbook MCP server runs under the `playbook-actuator` SA (the only identity bound to `nf-actuator`), an HTTPRoute publishes it on the course's own Gateway, and the AuthPolicy authenticates callers by ServiceAccount token and authorizes exactly `execution-agent`, with a RateLimitPolicy capping actuation. Agents do not get network power; one route does, and policy decides who may call it.
- `llama-stack.yaml`: the harness runtime the plan-judge uses (Responses-style API over the same Kimi model). It runs as a plain Deployment because the EA operator path is Removed; `enableServiceLinks: false` is load-bearing (the Service named `llama-stack` would otherwise inject `LLAMA_STACK_PORT=tcp://...` and crash the server's port parsing).

**Do (Console):** Import YAML, paste from `301-closed-loop-netops/deploy/ocp/rome/`, in this order: `state-store.yaml`, `fiveg-core.yaml`, `execution-rbac.yaml`, `execution.yaml`, `think-tank.yaml`, `netops-gateway.yaml`, `mcp-playbook.yaml`, `mcp-gateway-policies.yaml`, then `../302-energy-optimizer/deploy/ocp/rome/llama-stack.yaml`.

**Expect:** `loop-state`, `execution-agent`, `think-tank`, `mcp-playbook`, and `llama-stack` pods Ready (pip-at-startup makes first boot a few minutes each; `llama-stack` can take up to 10). AuthPolicy `mcp-playbook-authn` shows Accepted and Enforced. AMF/SMF/UPF pods Running in `fiveg-core`.

## Step 4: The agents

**Why:** the loop's four stages plus the judge, each an A2A server (they expose `/.well-known/agent.json`, the standard agent card) under its own ServiceAccount. Read the env blocks in the files; they ARE the wiring diagram: every agent gets the state store URL, Diagnostic additionally reads the Feast online store (101's anomaly verdicts are its input), Planning gets the think-tank MCP URL, the governed `/plan-score` gateway URL, the judge URL, and its two thresholds (`QUANT_RISK_CEILING`, the co-decision gate, and `HARD_RISK_FLOOR`, the rail no consensus can override), Validation gets Execution's URL to verify what actually happened. Separate Deployments per stage is deliberate: each stage scales, fails, and gets replaced independently, and each has exactly the permissions its stage needs.

**Do (Console):** Import YAML, paste `diagnostic.yaml`, `planning.yaml`, `validation.yaml`, `judge.yaml`.

**Expect:** all five agent Deployments (including `execution-agent` from Step 3) Ready within ~5 minutes.

## Step 5: Train, stage, and serve the risk model

**Why:** this is the **quantitative half of the co-decision**. LLM judges are persuadable; a GradientBoostingRegressor trained on 101's real per-minute KPI series is not. The training Job registers `netops-remediation-risk` in MLflow (expect r2 near 0.97, both reference clusters landed at 0.9714), the registry step makes it a named versioned asset, the stage Job copies the artifacts to MinIO, and KServe serves it exactly like 202's fraud model. The data ships as one gzipped ConfigMap because the four raw files would blow the 1 MB ConfigMap ceiling once base64-inflated; gzip fits the whole real dataset in ~380 KB, a practical trick worth remembering.

**Do:**

```
tar -czf /tmp/remdata.tgz -C ../101-noc-assistant/data \
  amf_metrics.csv smf_metrics.csv upf_metrics.csv alerts.json
oc create configmap remediation-train-data -n agent-school --from-file=data.tgz=/tmp/remdata.tgz
```

**Console:** Import YAML, paste `job-train-risk.yaml`. After it completes, verify the model in AI hub, Models, Registry (`netops-remediation-risk` v1) and promote it. Then paste `job-stage-risk.yaml` (log ends `UPLOADED n`), then `serving.yaml`.

**Expect:** train log shows `[eval] r2=0.97 mae=0.02` and `Created version '1'`; InferenceService `netops-remediation-risk` reaches **Ready**.

## Step 6: The governed scorer tool and the judge path

**Why:** the risk model must not be a private convenience of Planning; it is a governed tool. The `risk-scorer-mcp` server wraps the served model as an MCP tool, and the gateway policies publish it at `/plan-score` with authentication by SA token and authorization for exactly two identities: `planning-agent` and `plan-judge-agent`. Every plan score therefore passes a policy enforcement point with an identity attached, and the RateLimitPolicy caps scoring throughput. Try calling the route from any other pod and the gateway denies it; that denial is the feature.

**Do (Console):** Import YAML, paste `scorer-mcp.yaml`, then `scorer-gateway-policies.yaml`.

**Expect:** `risk-scorer-mcp` Ready; AuthPolicy `plan-scorer-authn` Accepted and Enforced.

## Step 7: Drive the loop

**Why:** the smoke chain proves the loop end to end: Diagnostic reads the feature store and writes its record to Redis, Planning pulls that record, consults the think-tank for the remediation flow, scores every step through the governed `/plan-score` route, co-decides with the judge, and stores the plan; the chain Job then reads the loop record back from Redis and asserts its status. With clean telemetry the correct plan is "no remediation needed" and the co-decision stays quiet, which is itself the lesson: an honest loop does nothing when nothing is wrong. The override drill afterward is the negative-then-positive discipline: tighten the quant gate until it holds a plan the judge clears, and watch the disagreement produce an audited override record instead of silent autonomy. Autonomy, consensus, override, and the hard rail all become **parameters in the experiment record**, which is what makes the loop reviewable.

**Do (Console):** Import YAML, paste `job-smoke-chain.yaml`.

**Expect:** the Job completes with `STATE_OK status=planned ...` and `CHAIN_OK <loop-id>` in its log.

For the full consensus and override evidence, follow `RUNBOOK-quantqual.md` step 6: drive the loop with an actionable scenario, read `[codecide]` lines from the planning-agent log, then `oc set env deploy/planning-agent QUANT_RISK_CEILING=0.3`, re-drive (quant now holds a medium-risk step, judge accepts, `override=true`, audited), and restore `QUANT_RISK_CEILING=0.66`. Both episodes land in Experiments `301-closed-loop` with params `judge_decision`, `quant_ok`, `override`, `hard_risk_rail`, `autonomous`.

The remaining smoke Jobs (`job-smoke-diagnostic.yaml`, `job-smoke-loop.yaml`, `job-smoke-validate.yaml`) exercise stages individually; run them the same way whenever you touch one stage.

## Troubleshooting

- An agent pod Ready but the loop stalls at Planning: check the think-tank pod in `think-tank` and its own `llm-credentials`.
- Gateway calls return 401/403: expected for any identity other than the ones in the AuthPolicy patterns; if a legit agent is denied, its SA name changed.
- Judge errors against llama-stack: confirm `llama-stack` is Ready and `enableServiceLinks: false` is still on both the judge and llama-stack Deployments.
- Builds or pipelines stuck: those are 101/202 prerequisites; see their manuals.

## Cleanup

Delete the smoke Jobs. The agents, gateways, and served model are the course deliverable; keep them for 302, which reuses llama-stack and the gateway.
