# 101 · OpenShift Deployment

The agent is a batch workload: one question, one Job, one exit code. The
CronJob variant gives you the continuous NOC sweep without a product
harness. CPU-only, restricted-PSS compliant, model served elsewhere
(RHOAI MaaS or vLLM) per the blueprint.

## 1. Project and credentials

```bash
oc new-project agent-school
oc create secret generic llm-credentials \
  --from-literal=LLM_BASE_URL=https://litellm-litemaas.apps.prod.rhoai.rh-aiservices-bu.com/v1 \
  --from-literal=LLM_API_KEY=sk-... \
  --from-literal=LLM_MODEL=Qwen3.6-35B-A3B
```

(Template: `../../shared/manifests/ocp/secret-llm.example.yaml`.)

## 2. Build the image

In-cluster, from your git fork (edit the `uri` in
`ocp/imagestream-buildconfig.yaml` first):

```bash
oc apply -k ocp/
oc start-build noc-assistant --follow      # or binary build:
# oc start-build noc-assistant --from-dir=.. --follow
```

Or locally with podman, pushing to the internal registry:

```bash
cd 101-noc-assistant
podman build -f deploy/Containerfile -t noc-assistant:latest .
podman tag noc-assistant:latest \
  default-route-openshift-image-registry.<cluster-domain>/agent-school/noc-assistant:latest
podman push default-route-openshift-image-registry.<cluster-domain>/agent-school/noc-assistant:latest
```

## 3. Run

One-shot question:

```bash
oc create -f ocp/job-ask.yaml
oc logs -f job/$(oc get jobs -o name --sort-by=.metadata.creationTimestamp | tail -1 | cut -d/ -f2)
```

Continuous sweep (applied suspended so it costs nothing until you enable it):

```bash
oc patch cronjob noc-sweep -p '{"spec":{"suspend":false}}'
```

## Notes

- The image bundles the real 5gprod dataset and runbooks; swapping to live
  telemetry means replacing `tools/lib.py` data access, nothing else.
- Pod security: `runAsNonRoot`, default seccomp, all capabilities dropped,
  no privilege escalation; the UBI9 Python image runs unprivileged and
  tolerates OpenShift's random UID.
- Set `LLM_WIRE_LOG=/tmp/wire.jsonl` in the Job env to capture wire-level
  QA evidence from in-cluster runs.

## Rome sandbox (verified in-cluster)

Deployed and run on the curriculum's Option-E reference platform (RHOAI 3.5
EA2 SNO; see `shared/manifests/vllm-rhoai.md`). The `ocp/rome/` overlay
generates `llm-credentials` pointing at the in-cluster Kimi-Linear endpoint,
so no laptop credentials are needed:

```bash
oc apply -k deploy/ocp/rome        # ImageStream/BuildConfig + suspended CronJob + Secret
oc start-build noc-assistant --follow
oc create -f deploy/ocp/job-ask.yaml
```

Notes from the live run (evidence: `../QA/rome_incluster_noc_job.log`):

- On a fresh SNO the internal image registry ships `managementState:
  Removed`; enable it once before the first git/binary build:
  `oc patch configs.imageregistry.operator.openshift.io/cluster --type=merge
  -p '{"spec":{"managementState":"Managed","storage":{"pvc":{"claim":"image-registry-storage"}}}}'`
  (create a 100Gi RWO PVC named `image-registry-storage` in
  `openshift-image-registry` first, backed by the LVMS StorageClass).
- The agent pod is CPU-only (100m/256Mi request); the model runs on the GPUs
  in another namespace — convention #1, made literal.
