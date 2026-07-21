"""Feature pipeline for 5gprod telemetry: engineer -> offline parquet -> push online.

Steps (the pieces the revenueassurance notebooks used to do by hand, as a
repeatable pipeline):

1. Load the per-NF telemetry (HuggingFace `fenar/5gcore-prod` when
   DATASET_SOURCE=hf, else the CSVs bundled with the course).
2. Engineer features: 1-hour rolling mean/min/max per KPI (the vectors the
   anomaly model trains and scores on).
3. Write the full engineered timeseries to data/<nf>_features.parquet —
   the offline source used by training/train_anomaly.py via
   `get_historical_features`.
4. Push the latest engineered row per NF to the Feast online store, so
   `get-online-features` serves the current network state to the agent.

With --score, also load the trained IsolationForest bundle (downloaded from
the MLflow run by training/train_anomaly.py) and push anomaly_score /
anomaly_flag per NF — the "anomaly inference run" of the data pipeline.

Run from the feature_repo directory (feature_store.yaml alongside).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

from features import AGGS, NF_KPIS  # noqa: F401  (shared column definitions)

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FEATURE_DATA_DIR", HERE / "data"))
COURSE_DATA = HERE.parent / "data"
HF_BASE = "https://huggingface.co/datasets/fenar/5gcore-prod/resolve/main"
WINDOW = 60  # samples ~= 1 hour at 1-minute resolution


def load_raw(nf: str) -> pd.DataFrame:
    if os.environ.get("DATASET_SOURCE", "local") == "hf":
        url = f"{HF_BASE}/{nf}_metrics.csv"
        df = pd.read_csv(url)
    else:
        df = pd.read_csv(COURSE_DATA / f"{nf}_metrics.csv")
    df["event_timestamp"] = pd.to_datetime(df.pop("timestamp"))
    return df.sort_values("event_timestamp").reset_index(drop=True)


def engineer(nf: str, df: pd.DataFrame) -> pd.DataFrame:
    kpis = NF_KPIS[nf]
    out = df[["event_timestamp"] + kpis].copy()
    roll = out[kpis].rolling(WINDOW, min_periods=1)
    for agg in ("mean", "min", "max"):
        stats = getattr(roll, agg)()
        for k in kpis:
            out[f"{k}_1h_{agg}"] = stats[k]
    out["nf"] = nf
    out["anomaly_score"] = 0.0
    out["anomaly_flag"] = 0
    return out


def _enable_model_tracing():
    """Optional: emit an MLflow trace per scoring call, linked to the
    anomaly-detector LoggedModel — the model's Traces tab in the RHOAI
    dashboard then shows every pipeline inference run (inputs, score, flag,
    latency) with model-version lineage. Needs the MLFLOW_* envs of the
    rome overlay; silently disabled elsewhere. Never breaks the pipeline."""
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        return None
    try:
        ws = os.environ.get("MLFLOW_WORKSPACE")
        if ws:
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
        import mlflow

        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT", "5gprod-anomaly"))
        mlflow.set_active_model(name=os.environ.get("LOGGED_MODEL_NAME", "anomaly-detector"))
        print("[trace] scoring traces -> LoggedModel 'anomaly-detector'")
        return mlflow
    except Exception as exc:
        print(f"[trace] disabled ({exc})")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", metavar="MODEL_BUNDLE",
                        help="joblib bundle {nf: (model, feature_cols, threshold)}; "
                             "score latest window and push anomaly features")
    parser.add_argument("--no-push", action="store_true",
                        help="only write offline parquet (no online push)")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    frames = {}
    for nf in NF_KPIS:
        eng = engineer(nf, load_raw(nf))
        frames[nf] = eng
        eng.to_parquet(DATA_DIR / f"{nf}_features.parquet", index=False)
        print(f"[ingest] {nf}: {len(eng)} rows -> {DATA_DIR}/{nf}_features.parquet")

    if args.score:
        import contextlib

        import joblib

        mlf = _enable_model_tracing()
        bundle = joblib.load(args.score)
        for nf, eng in frames.items():
            model, cols, threshold = bundle[nf]
            latest = eng.iloc[[-1]][cols]
            span_cm = (mlf.start_span(name=f"anomaly-inference/{nf}")
                       if mlf else contextlib.nullcontext())
            with span_cm as span:
                score = float(model.decision_function(latest)[0])
                flag = int(score < threshold)
                if span is not None:
                    span.set_inputs({"nf": nf,
                                     "event_timestamp": str(eng["event_timestamp"].iloc[-1]),
                                     "features": latest.iloc[0].round(3).to_dict()})
                    span.set_outputs({"anomaly_score": score, "anomaly_flag": flag,
                                      "threshold": threshold})
            eng.iloc[-1, eng.columns.get_loc("anomaly_score")] = score
            eng.iloc[-1, eng.columns.get_loc("anomaly_flag")] = flag
            print(f"[score] {nf}: anomaly_score={score:.4f} flag={flag}")

    if args.no_push:
        return

    from feast import FeatureStore

    store = FeatureStore(repo_path=str(HERE))
    for nf, eng in frames.items():
        latest = eng.tail(1).reset_index(drop=True)
        store.push(f"{nf}_push", latest, to=__import__(
            "feast").data_source.PushMode.ONLINE)
        print(f"[push] {nf}: latest vector ({latest['event_timestamp'].iloc[0]}) -> online store")


if __name__ == "__main__":
    main()
