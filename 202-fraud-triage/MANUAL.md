# Course 202 Execution Manual: Fraud Triage

A step-by-step guide for the 202 Revenue-Assurance course: train a fraud model with a Data Science Pipeline, serve it on KServe, and put a LangGraph triage agent in front of it with a real human-approval gate. Every stage is visible in the RHOAI dashboard.

Every step has three parts: **Why** explains what the step is for and what it means in the architecture, **Do** gives the exact action, and **Expect** gives the success signal.

## Just look at it (no deployment needed)

| What | Where |
|---|---|
| The pipeline and its green run | RHOAI dashboard (`rome` or `venice`), Develop & train, Pipelines, project `agent-school`, pipeline `fraud-brf-training` |
| The served model | AI hub, Models, Deployments, `fraud-detector` (project `agent-school`) |
| Registry entry | AI hub, Models, Registry, `revassurance-fraud-brf` |
| Triage runs (parked and escalated) | Experiments, experiment `revassurance-fraud` |
| Object storage | MinIO console `https://minio-console.apps.<cluster>.narlabs.io`, buckets `dspa-agent-school` and `models` |

## What you will build

A KFP v2 pipeline trains a BalancedRandomForest on the published 1M-row billing dataset and registers it in the workspace MLflow. A staging Job copies the model artifacts to MinIO so KServe's storage initializer can pull them. An MLServer runtime serves it speaking the V2 inference protocol. The triage agent scores real cases against the served model; escalations hit a LangGraph interrupt and PARK until a human approval token releases them. You prove the gate in both directions.

The big ideas of 202 are the **full MLOps chain** (pipeline to registry to object store to serving, each stage a named platform artifact) and **human-in-the-loop as an engineered control**, not a promise: the gate is code that provably stops the workflow.

## Prerequisites (verify before you start)

1. Everything from the 101 manual's prerequisites, plus project `agent-school` with `llm-credentials` and `mlflow-tracking` (101 Steps 1 and 2) and the SA-group MLflow binding (`mlflow-workspace-rbac.yaml`, 101 Step 4; the stage Job runs as the `default` SA and relies on it).
2. MinIO running in namespace `minio` with the root secret `minio-root` and bucket `models` (platform pack: `shared/manifests/rome/minio.yaml`).
3. The DSC has `aipipelines` Managed and the DSCI `trustedCABundle` carries the OpenShift service CA (needed for DSP-to-MLflow TLS; install report, cluster trust step).

## Step 1: MinIO credentials for the pipeline stack

**Why:** the pipeline server needs an object store for everything it produces (compiled pipelines, run artifacts, intermediate data), and it authenticates with a project-local Secret. This step copies the MinIO root credentials into the project under the exact key names (`accesskey`/`secretkey`) the DSPA schema expects. Copying data-to-data (never through a file on disk, never committed) is the course's standing rule for secrets: they are runtime wiring, not source code.

**Do:**

```
oc get secret minio-root -n minio -o json | jq '{apiVersion:"v1",kind:"Secret",
  metadata:{name:"dspa-minio-creds",namespace:"agent-school"},type:"Opaque",
  data:{accesskey:.data.MINIO_ROOT_USER,secretkey:.data.MINIO_ROOT_PASSWORD}}' | oc apply -f -
```

(Console alternative: read the two values in the `minio-root` Secret UI and create `dspa-minio-creds` with keys `accesskey`/`secretkey` via Import YAML.)

**Expect:** Secret `dspa-minio-creds` exists in `agent-school`.

## Step 2: RBAC and the pipeline bucket

**Why:** two authorization facts get established before anything runs. First, the KFP API sits behind kube-rbac-proxy, which authorizes every caller with a SubjectAccessReview on the `datasciencepipelinesapplications/api` resource; the Role+Binding here is what will let the in-cluster import Job (running as the `default` SA) talk to the pipeline server at all. Second, the pipeline's register step logs the trained model to workspace MLflow, and workflow pods run as `pipeline-runner-dspa`, so that SA gets the same namespace-scoped binding the agents use. The bucket Job then creates the DSPA's backing bucket; note `HOME=/tmp` inside it, the standing workaround for `mc` under OpenShift's random-UID security context.

**Do (Console):** Import YAML, paste `202-fraud-triage/deploy/ocp/rome/rbac.yaml`, then `job-make-bucket.yaml`.

**Expect:** Job `make-dspa-bucket` completes with `BUCKET_OK` in its log.

## Step 3: The pipeline server (DSPA)

**Why:** the DataSciencePipelinesApplication CR asks RHOAI to stand up a complete, project-scoped pipeline stack: API server, scheduler, persistence agent, Argo workflow controller, and a MariaDB, all owned by this workspace. Pipelines being **per-project** matters: your runs, artifacts, and metadata stay inside your governance boundary. Two EA findings are baked into the spec and worth reading in the file header: the API server trusts the OpenShift service CA (so it can call the managed MLflow over TLS), and the DSP-side MLflow integration is `DISABLED` because its task-level handler fails x509; lineage is unaffected since the pipeline's own register step logs to MLflow directly.

**Do (Console):** Import YAML, paste `202-fraud-triage/deploy/ocp/rome/dspa.yaml`.

**Expect:** in 2 to 3 minutes the `ds-pipeline-*` pods are all Running and the DSPA shows `Ready=True` (Console, project `agent-school`, search resource DataSciencePipelinesApplication). `WebhookReady=False` and `ManagedPipelineValid=False` with reason `NotApplicable` are cosmetic on this EA build. The dashboard Pipelines page for the project now works.

## Step 4: Import and run the pipeline

**Why:** the pipeline is compiled, uploaded, and started **in-cluster** by a Job, so a student needs no local KFP tooling and the exact same path works in CI. The training itself is the point of the course's ML half: a BalancedRandomForest on 1M real billing rows with 1.7% fraud, because naive training on that imbalance produces a useless model; the pipeline's steps (load, split, train, evaluate, register) each run as separate containers with their artifacts stored in the DSPA bucket, and the register step turns the result into a named MLflow model version. When the run goes green you have lineage from dataset to registered model, every step inspectable in the dashboard.

**Do:**

```
oc create configmap fraud-pipeline-src -n agent-school \
  --from-file=fraud_train_pipeline.py=202-fraud-triage/pipeline/fraud_train_pipeline.py \
  --from-file=import_and_run.py=202-fraud-triage/pipeline/import_and_run.py
```

**Console:** Import YAML, paste `202-fraud-triage/deploy/ocp/rome/job-import-pipeline.yaml`.

**Expect:** the import Job's log prints the pipeline id, version, and RUN_ID. Watch the run in the dashboard: Develop & train, Pipelines, Runs. It goes green (11/11) in 5 to 10 minutes and the register step prints fraud-class precision/recall near 0.996/0.999.

**If the run never starts** (workflow shows no status, `ds-pipeline-workflow-controller` restarts): this EA build's Argo controller blocks on a forbidden `clusterworkflowtemplates` informer and its own healthz then kills it. Apply the fix once per cluster, then delete the controller pod:

```
oc create clusterrole dsp-wc-clusterworkflowtemplates-read \
  --verb=get,list,watch --resource=clusterworkflowtemplates.argoproj.io
oc create clusterrolebinding dsp-wc-cwt-read-agent-school \
  --clusterrole=dsp-wc-clusterworkflowtemplates-read \
  --serviceaccount=agent-school:ds-pipeline-workflow-controller-dspa
oc delete pod -n agent-school -l app=ds-pipeline-workflow-controller-dspa
```

## Step 5: Stage the registered model to MinIO

**Why:** MLflow's registry stores the model where MLflow lives; KServe's storage initializer pulls models from S3. The stage Job is the bridge: it resolves the registered version (`MODEL_NAME`/`MODEL_VERSION` env), downloads the artifact tree from MLflow, and uploads it to `s3://models/revassurance-fraud-brf/1/`. Making this an explicit, auditable step (rather than serving straight out of the tracking server) is deliberate: the object store is the serving contract, and "which bytes is production running" has a one-line answer.

**Do:**

```
oc create configmap fraud-stage-src -n agent-school \
  --from-file=stage_model.py=202-fraud-triage/serving/stage_model.py
```

**Console:** Import YAML, paste `202-fraud-triage/deploy/ocp/rome/job-stage-model.yaml`.

**Expect:** Job `stage-fraud-model` log ends with `UPLOADED 7`. The artifacts now sit at `s3://models/revassurance-fraud-brf/1/` (check in the MinIO console).

## Step 6: Serve it on KServe

**Why:** three objects, one serving story. The **data connection Secret** carries the S3 endpoint and credentials in the annotation format KServe's storage initializer reads; the **ServingRuntime** defines how any MLflow-format model gets served here (stock UBI9 Python that pip-installs MLServer at startup, so there is no custom image to build or mirror); the **InferenceService** binds a specific model URI to that runtime. Serving through KServe (instead of wrapping the model in your own Flask pod) buys the platform behaviors the agent will rely on: the V2 inference protocol, dashboard visibility, probes, and a stable predictor Service. Read the file header's four EA findings; `MLSERVER_PARALLEL_WORKERS=0` and the headless-Service port rule will save you an afternoon.

**Do:** create the data connection (command in the header of `serving.yaml`):

```
oc get secret dspa-minio-creds -n agent-school -o json | jq '{apiVersion:"v1",kind:"Secret",type:"Opaque",
  metadata:{name:"aws-connection-minio-models",namespace:"agent-school",
    labels:{"opendatahub.io/dashboard":"true","opendatahub.io/managed":"true"},
    annotations:{"openshift.io/display-name":"minio-models","opendatahub.io/connection-type":"s3",
      "serving.kserve.io/s3-endpoint":"minio.minio.svc.cluster.local:9000",
      "serving.kserve.io/s3-usehttps":"0","serving.kserve.io/s3-region":"minio",
      "serving.kserve.io/s3-useanoncredential":"false"}},
  data:{AWS_ACCESS_KEY_ID:.data.accesskey,AWS_SECRET_ACCESS_KEY:.data.secretkey,
    AWS_S3_ENDPOINT:("http://minio.minio.svc.cluster.local:9000"|@base64),
    AWS_S3_BUCKET:("models"|@base64),AWS_DEFAULT_REGION:("minio"|@base64)}}' | oc apply -f -
```

**Console:** Import YAML, paste `202-fraud-triage/deploy/ocp/rome/serving.yaml` (ServiceAccount `fraud-serving`, ServingRuntime `fraud-mlserver`, InferenceService `fraud-detector`).

**Expect:** the predictor pod pip-installs at startup (~90s, absorbed by the startupProbe) and the InferenceService goes **Ready**. AI hub, Models, Deployments now lists `fraud-detector` with a proper runtime name.

## Step 7: Smoke the served model

**Why:** proof, not vibes. The smoke Job sends real rows from the published dataset through the live V2 endpoint and prints the served `fraud_probability`/`fraud_flag` next to the true labels. Passing this step means the whole chain (pipeline, registry, staging, serving) produced a model that answers correctly over the wire, before any agent depends on it. It is also your reference for the V2 request shape when you write your own clients.

**Do:**

```
oc create configmap fraud-infer-smoke-src -n agent-school \
  --from-file=infer_smoke.py=202-fraud-triage/serving/infer_smoke.py
```

**Console:** Import YAML, paste `job-infer-smoke.yaml`.

**Expect:** the log prints served predictions next to true labels for real dataset rows.

## Step 8: The triage agent, both directions

**Why:** the finale wires the agent to the served scorer and proves the human gate **in both directions**, mirroring the negative-then-positive discipline the 300-level courses formalize. The agent is a LangGraph state machine: score the case, decide, and for escalations hit an `interrupt` node that stops the graph cold. Run 1 has no approval token, so the two escalations PARK as `awaiting_approval`; that parked outcome is the evidence the gate is real code, not narrative. Run 2 supplies `APPROVE_TOKEN` (with an `APPROVER` identity), so the same cases resume and complete as `escalated`, with the approver recorded. Both runs land in Experiments, so the audit trail shows who approved what. A gate you have only ever seen open is not a gate.

**Do:**

```
oc create configmap triage-agent-src -n agent-school \
  --from-file=triage_agent.py=202-fraud-triage/agent/triage_agent.py
```

Run 1 (the gate PARKS): **Console:** Import YAML, paste `job-triage.yaml` as-is (no `APPROVE_TOKEN`).

**Expect:** log ends `TRIAGE_OK {"clear": 4, "awaiting_approval": 2}`.

Run 2 (approved): delete the Job, uncomment the `APPROVE_TOKEN` env line in the same file, paste again.

**Expect:** `TRIAGE_OK {"clear": 4, "escalated": 2}`, resumed with the approver identity. Both episodes are in Experiments under `revassurance-fraud`.

## Troubleshooting

- Import Job 403 against the KFP API: `rbac.yaml` missing (Step 2), or you changed the Job's SA.
- Predictor CrashLoop with `no current event loop`: you removed `MLSERVER_PARALLEL_WORKERS=0`.
- Storage initializer auth errors: the data connection secret's annotations or key names differ from Step 6.
- Pipeline run stuck: see the Step 4 controller fix.

## Cleanup

Delete the Jobs (TTL cleans them anyway). Delete the InferenceService to free the pod; keep the DSPA if 301 will run next (it reuses `dspa-minio-creds` and the SA patterns).
