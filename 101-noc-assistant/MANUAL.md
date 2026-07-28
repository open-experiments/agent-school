# Course 101 Execution Manual: NOC Assistant

A step-by-step guide for running the 101 NOC Assistant course on an OpenShift AI cluster. Follow it top to bottom and you will finish with a working LLM agent that watches a 5G core, backed by a Feast feature store, tracked in MLflow, with a distributed Ray tuning sweep admitted through Kueue.

Every step below has three parts: **Why** explains what the step is for and what it means in the architecture, **Do** gives the exact action, and **Expect** gives the success signal to check before moving on.

You do not have to deploy anything to study this course. The reference deployments run live on **Rome** and **Venice**; the "Just look at it" table below tells you where everything is.

## Just look at it (no deployment needed)

| What | Where |
|---|---|
| OpenShift console | `https://console-openshift-console.apps.rome.narlabs.io` or `.venice.` |
| RHOAI dashboard | `https://rh-ai.apps.rome.narlabs.io` or `.venice.` |
| Agent runs and traces | RHOAI dashboard, Develop & train, Experiments, workspace `agent-school` |
| Feature store | RHOAI dashboard, Feature store, project `fivegprod` |
| Ray sweep results | Experiments, `5gprod-anomaly-sweep` |
| Workload metrics (Kueue) | Observe & monitor, Workload metrics, project `agent-school` |
| The registered anomaly model | AI hub, Models, Registry, `5gprod-anomaly-isolationforest` |
| Everything Kubernetes | Console, project `agent-school` (Deployments, Jobs, ConfigMaps) |

## What you will build

The agent is a plain Kubernetes workload (Job or CronJob) that answers NOC questions. Its tools read live network state from a Feast online store. Every run is traced to the workspace MLflow. A RayJob sweeps IsolationForest contamination settings on a throwaway Ray cluster, admitted by Kueue.

The big idea of 101 is **Agent-as-a-Workload**: an agent is not a special platform object. It is a container with an identity, config, and an LLM endpoint, so everything Kubernetes already gives you (RBAC, quotas, audit, scheduling) applies to it unchanged.

## Prerequisites (verify before you start)

Work through this list in the dashboards. Every item must be true before Step 1.

1. OpenShift 4.22+ with RHOAI 3.5 EA2 installed from the **beta** channel, DataScienceCluster with dashboard, workbenches, aipipelines, kserve, modelregistry, feastoperator, ray, mlflowoperator all Managed, kueue **Unmanaged** (RHBOK operator installed instead). Full install narrative: `shared/manifests/rome/install-report.md`.
2. The internal image registry is configured. Check: Console, Administration, Cluster Settings, `configs.imageregistry` shows `managementState: Managed` with a storage PVC. If it shows `Removed`, builds will sit in `New` forever. Fix (admin): create a 100Gi PVC named `image-registry-storage` in `openshift-image-registry` on your default StorageClass, then set the config to Managed, replicas 1, rolloutStrategy Recreate, storage `pvc: {claim: image-registry-storage}`.
3. Kimi-Linear is served and Ready: RHOAI dashboard, AI hub, Models, Deployments shows `kimi-linear-48b-a3b` in project `telco-aix`. If not, deploy `shared/manifests/rome/vllm-kimi-linear.yaml` first (see its header; on a single 96G GPU use `--tensor-parallel-size=1 --max-num-seqs=64`, and on Blackwell nodes add `NCCL_P2P_DISABLE=1` and `--disable-custom-all-reduce` plus an 8Gi `/dev/shm` volume).
4. Managed MLflow is running: Console, project `redhat-ods-applications`, Deployment `mlflow` is Ready.
5. Kueue cluster objects exist: ResourceFlavors `default-flavor` and `nvidia-gpu-flavor`, ClusterQueue `default`, and the Kueue cluster CR frameworks list is exactly `["BatchJob","PyTorchJob","RayCluster","RayJob","TrainJob"]`. Never add Deployment/Pod/StatefulSet to that list; the webhook then breaks operator-owned Deployments.

You also need a checkout of this repo and either console access (kubeadmin or equivalent) or a logged-in `oc` terminal. Steps below say **Console** for clicks and give the `oc` fallback in one line.

## Step 1: Create the project

**Why:** the namespace is the agent's blast-radius boundary. Everything the course creates (identity, secrets, workloads, the feature store) lives inside it, so quota, RBAC, and cleanup all have one scope. The labels are not decoration: `kueue.openshift.io/managed` opts the namespace into Kueue admission (RHBOK only watches namespaces that ask), `opendatahub.io/dashboard` makes it a workspace the RHOAI dashboard (and its MLflow) recognizes, and `opendatahub.io/feast` lets the feast operator work here.

**Do (Console):** Administration, Namespaces, Create Namespace: name `agent-school`, labels:

```
kueue.openshift.io/managed=true
opendatahub.io/dashboard=true
opendatahub.io/feast=true
```

`oc` fallback: `oc new-project agent-school && oc label ns agent-school kueue.openshift.io/managed=true opendatahub.io/dashboard=true opendatahub.io/feast=true`

**Expect:** the project appears in the RHOAI dashboard project list.

## Step 2: Wiring secrets and config

**Why:** this is the agent's entire external configuration, expressed as standard Kubernetes objects. The Secret tells the agent where its LLM lives; because the model runs in-cluster, the "key" is a placeholder and no credential ever leaves the cluster. The two ConfigMaps are deliberately **optional** wiring (the workloads mount them with `optional: true`): with `mlflow-tracking` present every run is traced, without it the same image runs untraced; with `feature-store-client` present the agent's telemetry tools read the live Feast online store, without it they fall back to bundled CSVs. Config decides behavior, never a code change. That is the 12-factor discipline the whole series builds on.

**Do (Console):** click the **+** (Import YAML) button and paste, editing nothing:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: llm-credentials
  namespace: agent-school
type: Opaque
stringData:
  LLM_BASE_URL: http://kimi-linear-48b-a3b-predictor.telco-aix.svc.cluster.local:8080/v1
  LLM_API_KEY: none
  LLM_MODEL: kimi-linear-48b-a3b
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: mlflow-tracking
  namespace: agent-school
data:
  MLFLOW_TRACKING_URI: https://mlflow.redhat-ods-applications.svc:8443/mlflow
  MLFLOW_WORKSPACE: agent-school
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: feature-store-client
  namespace: agent-school
data:
  FEAST_ONLINE_URL: https://feast-fivegprod-online.agent-school.svc.cluster.local:443
```

Note the predictor Service is headless, so the port `:8080` in the URL is mandatory.

**Expect:** three green "created" toasts.

## Step 3: Base workload (build + agent identity)

**Why:** three objects, three lessons. The **ServiceAccount** is the agent's identity: the agent never runs as `default`, because identity is the hook every governance mechanism (MLflow workspace auth, NetworkPolicies, audit) keys off. The **ImageStream/BuildConfig** turn the course source into a container the OpenShift-native way: the cluster clones the public repo and builds the Containerfile itself, so no student laptop needs Docker. The **CronJob** shows the "continuous agent without a product harness" pattern: a NOC sweep every 15 minutes is just a schedule on the same image. It ships suspended so applying it costs no tokens until you decide otherwise.

**Do (Console):** Import YAML, paste the contents of these three files from `101-noc-assistant/deploy/ocp/base/`, in order: `serviceaccount.yaml`, `imagestream-buildconfig.yaml`, `cronjob-noc-sweep.yaml`. Add `namespace: agent-school` under each `metadata:` if you paste them raw.

`oc` fallback (this also generates Step 2's objects): `oc apply -k 101-noc-assistant/deploy/ocp/rome`

Then start the build. **Console:** Builds, BuildConfigs, `noc-assistant`, Actions, Start build. Watch the log; it clones `github.com/open-experiments/agent-school` and builds `deploy/Containerfile`.

**Expect:** Build `noc-assistant-1` reaches **Complete** (2 to 5 minutes). The CronJob `noc-sweep` exists but is **suspended**; leave it that way for now.

## Step 4: RBAC, queue, and the feature store

**Why:** this step grants and declares, it runs nothing. The two RBAC files let the agent's identity (and, for Ray later, the whole SA group of the namespace) pass MLflow's workspace SubjectAccessReview; without them tracking calls are denied, which is the platform proving that experiment data is governed by the same RBAC as everything else. The **LocalQueue** connects this namespace to the shared ClusterQueue so batch work here is admitted, counted, and visible in Workload metrics. The **FeatureStore CR** asks the feast operator to stand up a real feature-store service (online store + registry, each on its own PVC): the agent will not read files, it will query a service, which is what separates "demo with CSVs" from "platform with a data layer".

**Do (Console):** Import YAML, paste from `101-noc-assistant/deploy/ocp/rome/`: `mlflow-rbac.yaml`, `mlflow-workspace-rbac.yaml`, `kueue.yaml`, `featurestore-fivegprod.yaml` (add `namespace: agent-school` where missing).

**Expect:** within about a minute a pod `feast-fivegprod-...` is Running 1/1 in the project and two PVCs (`feast-fivegprod-registry`, `feast-fivegprod-online`) are Bound. The RHOAI dashboard Feature store page lists `fivegprod`.

EA note: on newer EA builds the feast CRD silently drops the `runFeastApplyOnInit` and `ui` spec keys. Ignore the pruning; the store still works because the next step applies feature definitions itself.

## Step 5: Load the feature store

**Why:** an empty feature store teaches nothing. This step runs the course's real data pipeline as one Job: engineer offline parquet from the published 5G core telemetry, `feast apply` the feature definitions (schema inference needs the parquet first, hence the order inside the Job), then push the latest feature vectors to the online store. After it, the store holds both halves that matter: **offline** point-in-time data for training without leakage, and **online** low-latency vectors for the agent's live reads. The second Job persists named SavedDatasets, which is how training data becomes a referenceable, versioned artifact instead of a loose file. The Jobs mount the store's PVCs directly because the EA registry server does not persist remote `feast apply`, a real-world workaround worth knowing.

**Do:** create the source ConfigMaps from a repo checkout:

```
oc create configmap feast-pipeline-src -n agent-school \
  --from-file=features.py=101-noc-assistant/feature_repo/features.py \
  --from-file=ingest.py=101-noc-assistant/feature_repo/ingest.py \
  --from-file=train_anomaly.py=101-noc-assistant/training/train_anomaly.py
oc create configmap feast-saveds-src -n agent-school \
  --from-file=save_datasets.py=101-noc-assistant/feature_repo/save_training_datasets.py
```

(No terminal? Import YAML works too: wrap each file's text in a ConfigMap `data:` block.)

**Console:** Import YAML, paste `101-noc-assistant/deploy/ocp/rome/job-feast-bootstrap.yaml`, then `job-feast-save-datasets.yaml`.

**Expect:** Job `feast-bootstrap` completes in 2 to 4 minutes. Its pod log ends with three `[push] ... -> online store` lines. `feast-save-datasets` completes and the dashboard Feature store, Datasets tab shows the three SavedDatasets.

## Step 6: Ask the agent something

**Why:** this is the course thesis in one object: **one agent run = one Job**. The agent starts, reads the question from env, calls its tools (which now hit the live online store), reasons with the in-cluster LLM, prints its answer, and exits. No server, no session, no state left behind; the Job record and the MLflow trace ARE the run. When you open the trace in Experiments you are seeing the governance payoff of Steps 2 to 4: identity passed auth, config enabled tracking, and every LLM and tool call is auditable after the fact.

**Do (Console):** Import YAML, paste `101-noc-assistant/deploy/ocp/job-ask.yaml` and add `namespace: agent-school`. (It uses `generateName`, so the console will accept it once; to re-run, paste it again. With a terminal: `oc create -f 101-noc-assistant/deploy/ocp/job-ask.yaml`.)

**Expect:** the `noc-ask-...` pod completes in about a minute. Read its log: a structured NOC answer that cites live KPI and alert data. Then open RHOAI dashboard, Experiments: the run appears with full LLM traces.

## Step 7: The distributed sweep (Ray + Kueue)

**Why:** classic ML work belongs on the same platform as the agent, under the same governance. The RayJob demonstrates two capabilities at once: **Ray** (via RHOAI's KubeRay) spins up a head+worker cluster that exists only for the sweep's lifetime and runs a 9-way contamination sweep for the anomaly model, and **Kueue** admits the whole thing through the queue you created in Step 4, so it shows up in Workload metrics like any governed batch workload. The sweep's results land in MLflow, closing the loop: the model the agent's `detect_anomalies` tool relies on was tuned, tracked, and admitted on-platform. The known EA quirk (KubeRay swaps the pod SA for a generated oauth-proxy SA) is why Step 4's group-level RBAC exists.

**Do:**

```
oc create configmap ray-sweep-src -n agent-school \
  --from-file=sweep.py=101-noc-assistant/training/ray_contamination_sweep.py
```

**Console:** Import YAML, paste `101-noc-assistant/deploy/ocp/rome/rayjob-anomaly-sweep.yaml`.

**Expect:** head and worker pods start (image pull can take a few minutes the first time), the RayJob reaches `SUCCEEDED`, results land in Experiments under `5gprod-anomaly-sweep`, and Observe & monitor, Workload metrics shows the admitted workload. Head and worker pods stay around until the 24h TTL; that is by design.

Optionally register the trained detector in the model registry: AI hub, Models, Registry, register model `5gprod-anomaly-isolationforest` from the MLflow model (see the live Rome/Venice registry entry for the metadata fields we fill in). The registry is where a model stops being an experiment artifact and becomes a named, versioned, promotable asset other courses can reference.

## Troubleshooting

- Build stuck in `New`: internal registry not configured. See prerequisite 2.
- Feast bootstrap fails to find `registry*.db`: the store pod has not finished its first boot. Wait for 1/1 Ready and re-create the Job.
- RayJob pods `PERMISSION_DENIED` against MLflow: KubeRay swaps in a generated oauth-proxy SA. `mlflow-workspace-rbac.yaml` (Step 4) grants the whole SA group; make sure it applied.
- Agent run has no Experiments entry: the `mlflow-tracking` ConfigMap is opt-in via `envFrom`; confirm it exists and re-run the Job.

## Cleanup

Delete the Jobs and RayJob (or wait for their TTLs). Suspend stays on for `noc-sweep` unless you enabled it. Deleting the project removes everything, including the Feast PVCs.
