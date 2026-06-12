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
