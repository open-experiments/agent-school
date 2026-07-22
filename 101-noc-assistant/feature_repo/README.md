# 101 · Feature repo (Feast on RHOAI)

The data pipeline of the course, agentified: what the Telco-AIX notebooks
did by hand becomes a Feast-backed pipeline the platform can run, audit,
and serve — and the RHOAI dashboard renders it under **Develop & train →
Feature store** (feature store `fivegprod`, entity `nf`, feature views
`amf_kpis`/`smf_kpis`/`upf_kpis`, feature service `noc_telemetry`).

Flow (all real, verified on Rome):

1. `ingest.py` loads the published dataset
   ([hf.co/datasets/fenar/5gcore-prod](https://huggingface.co/datasets/fenar/5gcore-prod)
   with `DATASET_SOURCE=hf`, the bundled CSVs otherwise), engineers 1-hour
   rolling mean/min/max per KPI, writes the offline parquet, and pushes the
   latest vector per NF to the online store.
2. `../training/train_anomaly.py` retrieves the same features point-in-time
   from the offline store, trains an IsolationForest per NF, validates
   against the labeled alert windows, and logs params/metrics/model to
   MLflow → **Experiments → 5gprod-anomaly** (Model training view).
2b. `save_training_datasets.py` persists the same point-in-time
   retrieval as named SavedDatasets in the registry (dashboard:
   Feature store -> Datasets) - the reproducibility artifact a model
   version can reference.
3. `ingest.py --score <bundle>` runs the pipeline's anomaly inference with
   the trained model and pushes `anomaly_score`/`anomaly_flag` online.
4. The agent (tools/lib.py) consumes the online store when
   `FEAST_ONLINE_URL` is set: `get_kpi_summary` serves the 1h aggregates,
   `detect_anomalies` serves the pipeline's model verdicts. Without the
   env, the CSV path is unchanged — laptop learners need no cluster.

## Rome ops notes (RHOAI 3.5 EA2 findings)

- The FeatureStore CR (`deploy/ocp/rome`-adjacent; created cluster-side)
  needs label `feature-store-ui: enabled` **and**
  `spec.services.registry.local.server.restAPI: true` before the dashboard
  lists it — the dashboard reads the registry through the
  `*-registry-rest` service.
- The EA registry server does not persist writes from remote `feast apply`;
  run apply/push as a Job that mounts the `feast-fivegprod-registry` and
  `feast-fivegprod-online` PVCs and targets the store files directly
  (`registry: /feast-registry/registry.db`, sqlite online store). This is
  the same access mode the operator's own CronJob uses.
- Set `spec.services.runFeastApplyOnInit: false` after first boot —
  otherwise every pod restart re-applies the operator's demo project,
  which **prunes** your objects (feast apply is destructive per project).
  Keep the init containers enabled; the servers need the scaffolding they
  create.
- `feast apply` needs the offline parquet present (schema/entity
  inference), so run `ingest.py --no-push` before the first apply.
- SavedDatasets: remote writes do not persist (same EA finding as
  above), so `save_training_datasets.py` runs as a Job on the store
  PVCs. Registry FileSources are relative paths and the offline parquet
  is per-Job, so the script rebuilds the engineered frame from the
  registered feature schema; saved parquet lands on the registry PVC
  (`/feast-registry/saved/`).
- The Entities tab shows a `__dummy` entity: that is Feast's own reserved
  placeholder (`DUMMY_ENTITY_NAME`, hardcoded in the library), auto-created
  in every project as the join target for entity-less feature views. Our
  views all join on the real `nf` entity, so it is inert here. Upstream
  Feast's UI filters it out of listings; the RHOAI 3.5 EA2 dashboard does
  not yet (cosmetic dashboard gap, worth reporting). Do not delete or
  rename it - `feast apply` recreates it.
