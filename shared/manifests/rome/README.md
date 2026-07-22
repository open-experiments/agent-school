# Rome platform pack — replicate the reference cluster

Everything needed to rebuild the Agent School reference platform on a new
cluster (next up: **Venice**, RTX PRO 6000 96GB). Rome's full install
narrative, including every failure mode hit and fixed, is in
[install-report.md](./install-report.md) — read it first; the manifests
here cover what was built *on top* of that base install.

## Order of operations

1. **Base platform** — follow [install-report.md](./install-report.md):
   operators (cert-manager, Service Mesh, RHOAI 3.5 EA `beta` channel,
   Kuadrant/RHCL, RHBOK Kueue, JobSet, LWS), DataScienceCluster with all
   components Managed (kueue Unmanaged, llamastackoperator Removed), MaaS
   gateway plumbing, GPU hardware profile.
2. **MinIO** — `oc apply -f minio.yaml`, create the `minio-root` secret,
   then buckets `models`, `pipelines`, `data` (mc Job pattern; see
   202-fraud-triage/deploy/ocp/rome/job-make-bucket.yaml for the template,
   noting `HOME=/tmp` under restricted SCC).
3. **Cluster trust** — add the OpenShift service CA to the RHOAI trust
   bundle (needed by DSP↔MLflow and anything else calling the managed
   MLflow over TLS):

   ```bash
   oc get cm openshift-service-ca.crt -n default -o jsonpath='{.data.service-ca\.crt}' > /tmp/service-ca.crt
   oc patch dscinitialization default-dsci --type merge \
     -p "{\"spec\":{\"trustedCABundle\":{\"managementState\":\"Managed\",\"customCABundle\":\"$(awk '{printf "%s\\n",$0}' /tmp/service-ca.crt)\"}}}"
   ```

4. **Managed MLflow tweaks** (namespace `redhat-ods-applications`) — Rome
   runs the workspace-scoped MLflow with server-side job execution on:

   ```bash
   oc set env deployment/mlflow -n redhat-ods-applications MLFLOW_SERVER_ENABLE_JOB_EXECUTION=true
   ```

   (EA2: the operator may reconcile this away; re-check after operator
   updates. The 201 LLM-judge runs client-side anyway — see
   201-rca-investigator/deploy/ocp/rome.)
5. **Model serving** — `vllm-kimi-linear.yaml` (ServingRuntime + KServe
   InferenceService). Stage weights in the MinIO `models` bucket and wire
   an S3 data connection first. **Venice sizing note:** Rome needs
   `--tensor-parallel-size=2` across 2× RTX 4090D (24G each) for
   Kimi-Linear-48B AWQ-8bit; a single RTX PRO 6000 (96G) fits it on one
   card — set `tensor-parallel-size=1`, `nvidia.com/gpu: 1`, and consider
   raising `--max-model-len` / running bf16 variants with the freed cards.
6. **Kueue for the workspace** — trim the Kueue cluster CR frameworks
   to `["BatchJob","PyTorchJob","RayCluster","RayJob","TrainJob"]` and
   label the namespace managed (exact commands and the LocalQueue in
   101's `deploy/ocp/rome/kueue.yaml`). WARNING: do not add
   Deployment/Pod/StatefulSet to the frameworks — Kueue's webhook then
   gates operator-owned Deployments and broke the DSPA's mariadb on
   Rome (details in that file's header). This lights up
   Observe & monitor → Workload metrics for the project.
7. **Courses** — per-course `deploy/ocp/rome` overlays: 101 (agent +
   Feast feature store + feast Jobs + RayJob calibration sweep), 201
   (RAG backend + judge Job), 202 (DSPA + fraud pipeline, then serving:
   stage-model Job → data connection → `serving.yaml` → infer-smoke
   Job). Apply each course's kustomization, then the one-shot Jobs in
   the order their comments state.

## What is intentionally *not* a manifest

- Secrets (minio-root, dspa-minio-creds, llm-credentials) — templates and
  creation commands live next to their consumers; never committed.
- The DSCI/DSC/MaaS cluster CRs — they are install-time artifacts covered
  by the install report (channels and webhook rules changed within EA;
  re-validate on the current build rather than blind-applying).

## Fidelity check against a live cluster

`export-live.sh` re-exports the hand-tuned live resources from a running
cluster so you can diff them against these manifests before replicating
(e.g., Rome's ServingRuntime wraps the image entrypoint in a small bash
shim to pre-create writable cache dirs; the env vars in the manifest make
that shim redundant, but diff before you trust).
