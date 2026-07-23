# 301 quant+qual co-decision — live run-book (Rome)

Everything for Stage 2b is built and offline-verified (real model
r² ≈ 0.97; arbiter truth table). This is the exact sequence to make it
**live on Rome** and capture the evidence, from a shell with `oc`
logged into the cluster. Run from this directory
(`301-closed-loop-netops/deploy/ocp/rome`).

Prereqs already on Rome from Track 4 / earlier courses: the
`netops-gateway`, the Authorino-TLS-disabled patch (see
`mcp-gateway-policies.yaml` header), `llama-stack` (OGX/Kimi), and the
`mlflow-tracking` ConfigMap + `dspa-minio-creds` Secret + `fraud-serving`
SA. Namespace: `agent-school`.

## 1 — Train + register the risk model
```sh
oc create configmap remediation-train-src -n agent-school \
  --from-file=train.py=../../../training/train_remediation_risk.py
tar -czf /tmp/remdata.tgz -C ../../../../101-noc-assistant/data \
  amf_metrics.csv smf_metrics.csv upf_metrics.csv alerts.json
oc create configmap remediation-train-data -n agent-school \
  --from-file=data.tgz=/tmp/remdata.tgz
oc apply -f job-train-risk.yaml
oc logs -f job/train-remediation-risk -n agent-school   # expect r2~0.97 + [register]
```
Verify `netops-remediation-risk` v1 in the Model Registry
(**AI hub → Models → Registry**) and promote it.

## 2 — Stage to MinIO + serve on KServe
```sh
oc create configmap remediation-stage-src -n agent-school \
  --from-file=stage_model.py=../../../serving/stage_model.py
oc apply -f job-stage-risk.yaml
oc logs -f job/stage-remediation-risk -n agent-school    # expect UPLOADED n
oc apply -f serving.yaml
oc get inferenceservice netops-remediation-risk -n agent-school -w  # -> Ready
```

## 3 — Governed scorer tool + gateway policies
```sh
oc create configmap risk-scorer-mcp-src -n agent-school \
  --from-file=server.py=../../../agents/scorer-mcp/server.py
oc apply -f scorer-mcp.yaml
oc apply -f scorer-gateway-policies.yaml
oc get authpolicy plan-scorer-authn -n agent-school \
  -o jsonpath='{.status.conditions[?(@.type=="Enforced")].status}{"\n"}'  # True
```

## 4 — The plan-judge
```sh
oc create configmap plan-judge-agent-src -n agent-school \
  --from-file=agent.py=../../../agents/judge/agent.py
oc apply -f judge.yaml
oc rollout status deploy/plan-judge-agent -n agent-school
# optional: python3 ../../../agents/judge/judge_smoke.py  (gentle vs restart-heavy)
```

## 5 — Roll Planning with the co-decision
```sh
oc create configmap planning-agent-src -n agent-school \
  --from-file=agent.py=../../../agents/planning/agent.py \
  --dry-run=client -o yaml | oc apply -f -
oc apply -f planning.yaml
oc rollout restart deploy/planning-agent -n agent-school
oc rollout status deploy/planning-agent -n agent-school
```

## 6 — Prove it, both directions (mirror 302)
Drive a loop through Diagnostic → Planning (the existing
`job-smoke-chain.yaml` / smoke client) and read the plan record:
```sh
oc logs deploy/planning-agent -n agent-school | grep '\[codecide\]'
# consensus: a gentle plan (scale_amf) -> autonomous=true, override=false
```
Force an **override** drill the same way 302 tightened its gate — make the
quant gate hold a plan the judge will clear:
```sh
oc set env deploy/planning-agent -n agent-school QUANT_RISK_CEILING=0.3
oc rollout status deploy/planning-agent -n agent-school
# re-drive the loop: quant now holds a medium-risk step, judge accepts
#  -> override=true, autonomous=true, override_note stored (audited)
oc set env deploy/planning-agent -n agent-school QUANT_RISK_CEILING=0.66  # restore
```
Confirm both episodes in **Experiments → 301-closed-loop** (params
`judge_decision`, `quant_ok`, `override`, `hard_risk_rail`, `autonomous`;
metric `max_step_risk`), and the `codecision` block in the plan record.

## 7 — Captures for the README + video (fresh, live)
- **Deployments** tab: `netops-remediation-risk` InferenceService Ready.
- **AuthPolicies**: `plan-scorer-authn` Accepted + Enforced (next to
  Track 4's `mcp-playbook-authn`).
- **Experiments** `301-closed-loop`: the consensus + override runs with
  the co-decision params.
- Model Registry: `netops-remediation-risk` v1 promoted.

Drop those into `301-closed-loop-netops/images/rhoai/` and add the
Stage 2b image refs to the README, then re-record the walkthrough with
the new scenes (spec in `video/specs/301.json`).
