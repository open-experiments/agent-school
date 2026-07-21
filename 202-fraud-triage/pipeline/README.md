# 202 · Fraud-model pipeline (Data Science Pipelines on RHOAI)

The agentification of the manual
[revenueassurance](https://github.com/open-experiments/Telco-AIX/tree/main/revenueassurance)
notebook flow: the platform runs it as a Kubeflow Pipelines v2 pipeline on
RHOAI Data Science Pipelines, visible per-stage in the dashboard under
**Develop & train → Pipelines** (project Agent School).

**Verified on Rome** — `fraud-brf-run-8`, all five stages green:
fraud-class precision 0.996 · recall 0.999 · F1 0.998 · ROC-AUC ~1.0 on
the 300,000-row held-out split; model registered as
`revassurance-fraud-brf` v1 in the workspace MLflow.

## Files

- `fraud_train_pipeline.py` — the KFP v2 DSL. Five components, each a
  UBI9 python-311 container: `load_data` (pulls
  [fenar/revenue_assurance](https://huggingface.co/datasets/fenar/revenue_assurance),
  1,000,000 billing records, 1.7% fraud) → `preprocess` (encode
  `Plan_Type`, stratified 70/30 split) → `train` (imbalanced-learn
  BalancedRandomForest — the source experiment's model family) →
  `evaluate` (fraud-class metrics as KFP Metrics) → `register`
  (params/metrics/pyfunc model to the managed MLflow, registers
  `revassurance-fraud-brf` — the same workspace-header/SA-token handshake
  101/201 use).
- `fraud_train_pipeline.yaml` — compiled IR (`python fraud_train_pipeline.py`).
- `import_and_run.py` — in-cluster importer: compiles the DSL, uploads the
  pipeline (or a new version) through kube-rbac-proxy, starts a run. Ran
  as `../deploy/ocp/rome/job-import-pipeline.yaml`.

## Rome ops notes (RHOAI 3.5 EA2 findings)

- **Pipeline server**: `deploy/ocp/rome/dspa.yaml` — DSPA v2 backed by the
  cluster MinIO (bucket `dspa-agent-school`, created by
  `job-make-bucket.yaml`). All conditions Ready on the SNO.
- **API access from workloads**: the KFP API's plain port (8888) is
  NetworkPolicy-restricted to DSP's own pods. Go through kube-rbac-proxy
  on 8443: SA token as Bearer, TLS trust from the pod's `service-ca.crt`,
  and the caller's SA needs `datasciencepipelinesapplications/api`
  (`deploy/ocp/rome/rbac.yaml`).
- **Workflow pods & MLflow**: steps run as `pipeline-runner-dspa`; the
  `register` component passes MLflow's SubjectAccessReview via the
  namespace-scoped `edit` binding (`rbac.yaml`).
- **Task-level MLflow plugin (EA defect + workaround)**: EA2's DSP ships
  an MLflow plugin that auto-creates a parent MLflow run per pipeline run
  and nested runs per task. The *task-level* handler's HTTP client does
  not honor the mounted CA bundle, fails
  `x509: certificate signed by unknown authority` against the managed
  MLflow's service-CA cert, and — because plugin completion is fatal —
  fails every component even after its real work succeeded. Adding the
  service CA via `DSCInitialization spec.trustedCABundle.customCABundle`
  fixes the run-level handler only. Workaround until the plugin TLS wiring
  is fixed: `spec.mlflow.integrationMode: DISABLED` on the DSPA
  (`dspa.yaml`). Lineage is unaffected — `register` logs directly.
- **MLflow artifact uploads under workspace scoping**: MLflow 3.4's
  `http_artifact_repo` bypasses `rest_utils.http_request`, so the
  workspace-header shim alone gets `400 Workspace context is required`
  on `log_model`. The `register` component therefore also injects
  `X-MLFLOW-WORKSPACE` at the `requests.Session` level (see the code
  comment) — tracking *and* artifact calls both carry the header.
