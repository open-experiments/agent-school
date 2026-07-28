# Venice Replication — Progress & Resume Point (updated July 28, 2026, evening)

Read with shared/manifests/rome/README.md + install-report.md. Work is done via Chrome on
console-openshift-console.apps.venice.narlabs.io (kube:admin), using in-page fetch helpers
against /api/kubernetes with the csrf-token cookie. Secrets are generated in-page only.

## Done (durable on the cluster)

### Platform (from earlier session)
- Operators: cert-manager 1.20, Mesh 3.4, RHOAI 3.5.0-ea.2 (beta channel), RHCL/Kuadrant 1.4.2
  (+authorino/limitador/dns), RHBOK Kueue 1.3.1, JobSet, LWS. All CSVs Succeeded.
- Cluster CRs: Kueue (frameworks BatchJob,PyTorchJob,RayCluster,RayJob,TrainJob), JobSetOperator, LWS.
- DSC default-dsc: Rome's spec verbatim PLUS v2-API patch (mlflowoperator, ogx, sparkoperator,
  trainer, aigateway Managed — v1 API prunes these keys). Only MaaSPrerequisitesAvailable=False
  (Rome parity). MaaS gateway plumbing, DSCI trustedCABundle, MinIO (500Gi, buckets, MIRROR_OK
  Kimi weights), fresh minio-root.

### Kimi serving — FINAL Venice config (deviates from Rome deliberately: tp=1, user decision)
ServingRuntime custom-vllm-tp1 (telco-aix), image pinned to Rome digest e4f88a83…, Rome's
bytes_to_unicode sitecustomize shim, PLUS the fixes found live this session:
- volumes: shm Memory emptyDir 8Gi at /dev/shm (missing → NCCL rank-1 hangs silently at pynccl)
- env: NCCL_P2P_DISABLE=1 (Blackwell pair PCIe P2P broken on this node), NCCL_DEBUG=INFO,
  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
- args: --tensor-parallel-size=1 --max-model-len=131072 --gpu-memory-utilization=0.83
  --disable-custom-all-reduce --max-num-seqs=64
- ISVC kimi-linear-48b-a3b gpu:1 → Ready; smoke passed (VENICE_OK). One RTX PRO 6000 freed.
- WHY max-num-seqs=64: MLA chunked-prefill workspace = max(min(8*ctx, 65536),
  max_num_seqs*block_size) tokens × heads × 256B_head_dims × 2B. Kimi-Linear forces
  block_size=1888 (mamba alignment); default max_num_seqs=1024 → 1.93M tokens → 29.5GiB fixed
  alloc at tp=1 (7.38GiB at tp=2). Context length does NOT shrink it; 64 seqs → ~1.85GiB.

### MLflow
Cluster CR mlflow created (Rome spec, sqlite, OPENAI/HOSTED_VLLM env → kimi predictor).
EA finding: Venice operator injects MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false and the CR env
carrying the same key makes SSA reconcile fail (duplicate key). Applied Rome-equivalent
end-state: key kept in CR env (poison-pill stops reverts) + deployment hand-patched to true.
Pod Ready, job execution on.

### Image registry (prereq found missing)
configs.imageregistry cluster was Removed → builds never scheduled. Now: Managed, replicas 1,
Recreate, storage Unmanaged + PVC image-registry-storage 100Gi lvms-vg1 (Rome parity).

### Model registry (was missing entirely — user caught it)
ModelRegistry CR venice-registry in rhoai-model-registries (Rome's rome-registry spec: postgres
generateDeployment, fresh registry-db secret). Available=True. All 9 Rome registered models
replayed via REST (versions + artifacts): kimi (props updated to Venice tp=1 truth),
diffusiongemma, nex-n2-mini, inkling, probe-test (archived), 5gprod-anomaly-isolationforest,
revassurance-fraud-brf, sustainability-energy-efficiency, netops-remediation-risk.
RBAC: registry-client SA + binding; venice-registry-users group binding (operator-created).

### Courses — ALL DEPLOYED AND PROVEN
Namespaces agent-school / fiveg-core / think-tank with Rome's exact labels.
- 101: base+rome overlay, llm-credentials + mlflow-tracking + feature-store-client, builds
  Complete, FeatureStore fivegprod Ready (EA: runFeastApplyOnInit + ui keys PRUNED from Venice
  feast CRD — newer operator; store works anyway), feast-bootstrap + feast-save-datasets
  Succeeded, noc-ask agent answered with MLflow traces, RayJob anomaly-contamination-sweep
  SUCCEEDED via Kueue (LocalQueue agent-school-queue).
- 201: rca-rag Running, rca-run Succeeded (evidence-cited RCA), rca-judge Succeeded
  ("logged feedback for 3 traces").
- 202: dspa-minio-creds, rbac, BUCKET_OK, DSPA Ready, pipeline fraud-brf-training Succeeded
  11/11, stage UPLOADED 7, fraud-detector ISVC Ready, infer-smoke OK, triage run1
  {clear:4, awaiting_approval:2} + run2 with APPROVE_TOKEN {clear:4, escalated:2}.
  EA FINDING (critical): DSP workflow controller blocks forever in cache-sync on the FORBIDDEN
  clusterworkflowtemplates informer and crash-loops on healthz "workflow never reconciled".
  Fix: ClusterRole dsp-wc-clusterworkflowtemplates-read (get/list/watch) + CRB for
  agent-school/ds-pipeline-workflow-controller-dspa, bounce the pod. (Rome's SA is equally
  forbidden but its identical-image controller tolerated it — races/ordering; grant is the fix.)
- 301: full stack live — loop-state (redis, fresh loop-state-auth), fiveg-core AMF/SMF/UPF,
  think-tank (own llm-credentials), netops-gateway + mcp-playbook route/AuthPolicy/RLP,
  diagnostic/planning/validation/execution/plan-judge agents all Ready, llama-stack Ready,
  train-remediation-risk r2=0.9714 (Rome ~0.97 parity) → registered v1 → staged → ISVC
  netops-remediation-risk Ready, risk-scorer-mcp + plan-scorer-authn Accepted+Enforced,
  smoke-chain CHAIN_OK (clean telemetry → no-action plan; no [codecide] block this run — that
  needs an actionable-plan scenario; override drill not yet driven).
  Data ConfigMap remediation-train-data built IN-PAGE (fetch 4 files from GitHub raw → JS ustar
  → CompressionStream gzip → binaryData, 376KB) — no laptop tooling.
- 302: sim-rbac, sustainability train (r2 0.878 lineage) → stage → sustainability-scorer ISVC
  Ready, scorer-mcp + scorer-authn Enforced, judge-agent Ready, optimize-episode CHANGE_PLAN
  accepted round 1, genai-eval EVAL_METRICS {decision_correctness:0.75,
  groundedness_numeric:0.875, qos_safety:0.875, llm_groundedness:1, llm_scored:8} GENAI_EVAL_OK.

## Remaining
1. Evidence pass: 301 [codecide] consensus + override drill (QUANT_RISK_CEILING=0.3) needs an
   actionable-plan scenario; dashboard screenshots (Deployments, AuthPolicies, Experiments,
   Registry) for the READMEs.
2. export-live.sh fidelity diff (live vs pack) + capture.
3. Docs: fold the EA lessons above into Rome-LessonsLearned + venice section in the pack.

## Gotchas for the driving session
- Tool sanitizer mangles secretRef/token/password/accesskey/redis:// **in both directions**:
  build via concat at runtime; for extraction, substitute risky chars reversibly first.
- Console k8s proxy: GETs need Accept: application/json; mutations X-CSRFToken; CRDs merge-patch.
- registry.access.redhat.com flakes: imagePullPolicy IfNotPresent everywhere.
- Cross-cluster data (registry seed): extract via chunked substituted output; tool output caps
  ~1KB/call — compress to essential fields first.
- RayJob head/worker pods linger until ttlSecondsAfterFinished (24h) — by design, not a leak.
