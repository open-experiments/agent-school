"""Anomaly-model training for 5gprod telemetry: Feast offline -> train ->
test/validate -> MLflow (RHOAI Experiments, Model training view).

The agentified version of the manual notebook flow (Telco-AIX): features
come from the Feast offline store via point-in-time `get_historical_features`
(the same engineered vectors the online store serves to the agent), an
IsolationForest is trained per NF on the earliest 70% of the timeline, and
validated on the remaining 30% against the labeled incident windows in
data/alerts.json (rows inside an alert window for that NF are ground-truth
anomalies). Params, per-NF precision/recall/F1, and the model bundle are
logged to MLflow; the run shows up in the RHOAI dashboard under
Experiments -> 5gprod-anomaly (Model training).

The saved bundle {nf: (model, feature_cols, threshold)} is what
feature_repo/ingest.py --score uses for the pipeline's inference run.

Requires: feature_repo/ingest.py has run (offline parquet present), and the
MLFLOW_* / workspace envs of the rome overlay.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score

HERE = Path(__file__).resolve().parent
COURSE = HERE.parent
sys.path.insert(0, str(COURSE / "feature_repo"))
from features import NF_KPIS  # noqa: E402

TRAIN_FRACTION = 0.7
CONTAMINATION = float(os.environ.get("ANOMALY_CONTAMINATION", "0.05"))


def _workspace_shim() -> None:
    """Workspace-scoped MLflow on RHOAI; same shim as agent/_enable_mlflow."""
    ws = os.environ.get("MLFLOW_WORKSPACE")
    if not ws:
        return
    if not os.environ.get("MLFLOW_TRACKING_TOKEN"):
        sa = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        if sa.exists():
            os.environ["MLFLOW_TRACKING_TOKEN"] = sa.read_text().strip()
    os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
    from mlflow.utils import rest_utils

    _orig = rest_utils.http_request

    def _shim(host_creds, endpoint, method, *a, **kw):
        headers = dict(kw.pop("extra_headers", None) or {})
        headers["X-MLFLOW-WORKSPACE"] = ws
        if endpoint == "/v1/traces" and host_creds.host.rstrip("/").endswith("/mlflow"):
            import copy

            host_creds = copy.copy(host_creds)
            host_creds.host = host_creds.host.rstrip("/")[: -len("/mlflow")]
        return _orig(host_creds, endpoint, method, *a, extra_headers=headers, **kw)

    rest_utils.http_request = _shim
    from mlflow.store.tracking import rest_store

    rest_store.http_request = _shim


def historical_features(nf: str) -> pd.DataFrame:
    """Point-in-time retrieval from the Feast offline store (file source)."""
    from feast import FeatureStore

    repo = COURSE / "feature_repo"
    store = FeatureStore(repo_path=str(repo))
    parquet = pd.read_parquet(
        Path(os.environ.get("FEATURE_DATA_DIR", repo / "data")) / f"{nf}_features.parquet")
    entity_df = parquet[["event_timestamp"]].copy()
    entity_df["nf"] = nf
    cols = [c for c in parquet.columns if c not in
            ("event_timestamp", "nf", "anomaly_score", "anomaly_flag")]
    feature_refs = [f"{nf}_kpis:{c}" for c in cols]
    df = store.get_historical_features(entity_df=entity_df, features=feature_refs).to_df()
    return df.sort_values("event_timestamp").reset_index(drop=True)


def label_from_alerts(nf: str, timestamps: pd.Series) -> pd.Series:
    alerts = json.loads((COURSE / "data" / "alerts.json").read_text()).get("alerts", [])
    labels = pd.Series(0, index=timestamps.index)
    for alert in alerts:
        if alert["component"].lower() != nf:
            continue
        start = pd.Timestamp(alert["start_time"])
        end = pd.Timestamp(alert["end_time"])
        labels[(timestamps >= start) & (timestamps <= end)] = 1
    return labels


def main() -> None:
    _workspace_shim()
    import mlflow

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT", "5gprod-anomaly"))

    bundle, metrics_all = {}, {}
    with mlflow.start_run(run_name="isolation-forest-5gprod") as run:
        mlflow.log_params({
            "algorithm": "IsolationForest",
            "contamination": CONTAMINATION,
            "train_fraction": TRAIN_FRACTION,
            "window": "1h rolling mean/min/max",
            "feature_source": "feast:fivegprod (offline file source)",
            "dataset": "hf.co/datasets/fenar/5gcore-prod",
        })
        for nf in NF_KPIS:
            df = historical_features(nf)
            ts = pd.to_datetime(df["event_timestamp"]).dt.tz_localize(None)
            feature_cols = [c for c in df.columns if c not in ("event_timestamp", "nf")]
            X = df[feature_cols]
            y = label_from_alerts(nf, ts)

            split = int(len(df) * TRAIN_FRACTION)
            model = IsolationForest(
                n_estimators=200, contamination=CONTAMINATION, random_state=42)
            model.fit(X.iloc[:split])

            scores = model.decision_function(X.iloc[split:])
            threshold = 0.0  # IsolationForest decision boundary
            y_pred = (scores < threshold).astype(int)
            y_true = y.iloc[split:]
            m = {
                f"{nf}_precision": precision_score(y_true, y_pred, zero_division=0),
                f"{nf}_recall": recall_score(y_true, y_pred, zero_division=0),
                f"{nf}_f1": f1_score(y_true, y_pred, zero_division=0),
                f"{nf}_test_anomaly_rate": float(y_pred.mean()),
                f"{nf}_test_rows": int(len(y_true)),
            }
            mlflow.log_metrics(m)
            metrics_all.update(m)
            bundle[nf] = (model, feature_cols, threshold)
            print(f"[train] {nf}: " + " ".join(f"{k.split('_',1)[1]}={v:.3f}"
                  for k, v in m.items() if isinstance(v, float)))

        out = Path(os.environ.get("MODEL_OUT", "/tmp/anomaly_bundle.joblib"))
        joblib.dump(bundle, out)
        mlflow.log_artifact(str(out), artifact_path="model")
        print(f"[train] bundle -> {out}; run_id={run.info.run_id}")
        print(f"MLFLOW_RUN_ID={run.info.run_id}")


if __name__ == "__main__":
    main()
