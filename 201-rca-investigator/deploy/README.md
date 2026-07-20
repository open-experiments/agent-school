# 201 · OpenShift Deployment

Two workloads from one image (one codebase, two roles): the RAG retrieval
backend runs as a Deployment + Service (pattern 2: the index lives in the
backend, never in the agent pod), and each RCA is a one-shot Job that calls
it over the Service DNS name.

## 1. Project and credentials

Same namespace and `llm-credentials` secret as 101 (the 201 agent also reads
`LLM_MODEL_SMALL` / `LLM_MODEL_LARGE`; the example secret sets both).

## 2. Build the image

The image needs the 101 dataset, so the build context is the **repo root**:

```bash
oc apply -k ocp/base                               # ImageStream + BuildConfig + RAG service
oc start-build rca-investigator --from-dir=../.. --follow   # binary build from repo root
# or git build: edit uri in ocp/base/imagestream-buildconfig.yaml, then ConfigChange triggers it
```

Local podman equivalent (from the repo root):

```bash
podman build -f 201-rca-investigator/deploy/Containerfile -t rca-investigator:latest .
```

## 3. Verify the backend, then run an RCA

```bash
oc rollout status deploy/rca-rag
oc run curl --rm -it --image=registry.access.redhat.com/ubi9/ubi-minimal -- \
  curl -s http://rca-rag:8201/healthz        # expect {"status":"ok","documents":4330}

oc create -f ocp/job-rca.yaml
oc logs -f job/$(oc get jobs -o name --sort-by=.metadata.creationTimestamp | tail -1 | cut -d/ -f2)
```

The cited RCA report prints to the Job logs and is written to `/reports`
(emptyDir; mount a PVC at that path if reports must outlive the pod).

## Notes

- `RAG_URL=http://rca-rag:8201` wires agent → backend across pods; moving
  the backend (other namespace, other cluster, SaaS) is an env change,
  which is the pattern-2 lesson restated in YAML.
- The Job ships with `RCA_MAX_TURNS=4` and `RCA_REPORT_MAX_TOKENS=1200`;
  tune per cost/latency budget. `RCA_DISABLE_THINKING=1` is the default in
  code (see QA/README finding #2 on Qwen hybrid reasoning).
- Restricted-PSS compliant: non-root, default seccomp, no capabilities,
  no privilege escalation; probes on `/healthz`.

## Rome sandbox (verified in-cluster)

Deployed on the Option-E reference platform (see
`shared/manifests/vllm-rhoai.md`). The `ocp/rome/` overlay brings up the RAG
backend and generates `llm-credentials` for the in-cluster Kimi endpoint:

```bash
oc apply -k deploy/ocp/rome        # base + Secret + mlflow-tracking ConfigMap + RBAC
oc start-build rca-investigator --follow
oc rollout status deploy/rca-rag
oc create -f deploy/ocp/job-rca.yaml
```

On Rome the overlay also wires **Experiments tracking**: each RCA Job lands
in the RHOAI dashboard under **Experiments → 201-rca-investigator**, with
traces for both phases (the small-model tool loop and the large-model
write-up). Mechanics — workspace header, ServiceAccount-token auth, RBAC —
are identical to 101; see the 101 deploy README's Experiments section.

### LLM-as-a-Judge (self-hosted judge on the platform's own model)

The `evidence-grounding` judge (Experiments → 201-rca-investigator →
Judges) scores every RCA trace for citation discipline — the course's core
contract — using **Kimi-Linear itself** as the judge via LiteLLM's
`hosted_vllm` provider. No external LLM API is involved: the platform
audits its own agent with its own served model.

Run it with `eval/judge_evidence_grounding.py` (in-cluster Job or any pod
with the rome env); verdicts land on the traces as `evidence-grounding`
feedback, visible in each trace's Assessments panel. Verified run: the
final RCA report trace judged `yes` (all claims cited), intermediate
tool-loop traces judged `no` — exactly the discrimination you want.

RHOAI 3.5 EA2 findings the script works around (kept here as EA feedback):

1. **Spans must be in the tracking store** for server-side judges, but the
   MLflow client derives the OTLP ingest URL as `<tracking-uri>/v1/traces`
   while RHOAI serves that route at the server root — the resulting 404
   silently falls back to artifact storage. The agents' `_enable_mlflow()`
   rewrites the host for `/v1/traces` (and patches `rest_store`'s
   import-time binding of `http_request`).
2. The dashboard's **"Run judges" server job** is unusable in EA2: the jobs
   status API 404s under workspace scoping, and job execution ships
   disabled (`MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false`, operator-pinned).
3. The `openai:/` judge adapter **ignores `OPENAI_API_BASE`** and hardcodes
   api.openai.com; use `hosted_vllm:/<model>` + `HOSTED_VLLM_API_BASE`
   (LiteLLM path) for self-hosted vLLM judges.
4. The MLflow operator's NetworkPolicy blocks egress to model endpoints on
   non-443 ports; `spec.networkPolicyAdditionalEgressRules` on the `MLflow`
   CR opens TCP 8080 to the serving namespace.

Live run (evidence: `../QA/rome_incluster_rca_job.log`,
`../QA/rome_incluster_rca_report.md`): the RCA Job resolved `rca-rag:8201`
across pods, made 5 tool calls into the backend, and produced an RCA with
49 record citations. The RAG Deployment and the agent Job are separate pods
on separate lifecycles — the pattern-2 split from the blueprint, running.

(If the internal registry is disabled on a fresh SNO, enable it first — see
the 101 deploy README's Rome section.)
