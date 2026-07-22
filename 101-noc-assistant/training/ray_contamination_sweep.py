"""Contamination calibration sweep for the 101 anomaly model — Ray on Kueue.

Nine Ray tasks (3 NFs x 3 contamination values) train and evaluate an
IsolationForest on the published 5gcore-prod telemetry, score each config
against the labeled alert windows (F1) and against the labeled incident
rate (rate gap), and log everything to MLflow (workspace agent-school,
experiment `5gprod-anomaly-sweep`). Real distributed compute, real data,
real tracking — admitted through Kueue (LocalQueue `agent-school-queue`).

Runs as deploy/ocp/rome/rayjob-anomaly-sweep.yaml: the RayJob carries the
`kueue.x-k8s.io/queue-name` label, so RHBOK admits it against the shared
ClusterQueue before KubeRay spins up the head+worker pods. Pods run as
the `noc-assistant` ServiceAccount so MLflow's workspace
SubjectAccessReview passes (same RBAC as the agent itself).

Objective note: on this dataset the IsolationForest decision boundary
rarely aligns with the labeled windows (F1 near zero across the grid —
also visible in the training runs), so the calibration objective is the
labeled incident *rate*: pick the contamination whose predicted anomaly
rate is closest to the observed alert-window rate. F1/precision/recall
are still logged per config for the honest picture.
"""
import json
import os
import urllib.request

import ray

HF = "https://huggingface.co/datasets/fenar/5gcore-prod/resolve/main"
GRID = [0.02, 0.05, 0.10]
NFS = ["amf", "smf", "upf"]
WINDOW, SPLIT = 60, 0.7


def fetch(url, tries=6):
    """Backoff fetch — nine parallel tasks hitting HF directly trips its
    rate limiter (learned live: HTTP 429 failed the first run). The driver
    downloads once and shares bytes via the Ray object store instead."""
    import time
    for i in range(tries):
        try:
            return urllib.request.urlopen(url).read()
        except Exception:
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"failed to fetch {url}")


@ray.remote(num_cpus=1)
def evaluate(nf, contamination, csv_bytes, alerts):
    import io

    import pandas as pd
    from sklearn.ensemble import IsolationForest
    from sklearn.metrics import f1_score, precision_score, recall_score

    df = pd.read_csv(io.BytesIO(csv_bytes))
    df["event_timestamp"] = pd.to_datetime(df.pop("timestamp"))
    df = df.sort_values("event_timestamp").reset_index(drop=True)
    kpis = [c for c in df.columns if c != "event_timestamp"]
    out = df[kpis].copy()
    roll = out.rolling(WINDOW, min_periods=1)
    for agg in ("mean", "min", "max"):
        s = getattr(roll, agg)()
        for k in kpis:
            out[f"{k}_1h_{agg}"] = s[k]

    labels = pd.Series(0, index=df.index)
    for a in alerts:
        if str(a.get("component", "")).lower() != nf:
            continue
        m = (df["event_timestamp"] >= pd.Timestamp(a["start_time"])) & \
            (df["event_timestamp"] <= pd.Timestamp(a["end_time"]))
        labels[m] = 1

    n = int(len(out) * SPLIT)
    clf = IsolationForest(n_estimators=200, contamination=contamination,
                          random_state=42)
    clf.fit(out.iloc[:n])
    pred = (clf.decision_function(out.iloc[n:]) < 0).astype(int)
    true = labels.iloc[n:]
    rate_pred, rate_true = float(pred.mean()), float(true.mean())
    return {"nf": nf, "contamination": contamination,
            "f1": float(f1_score(true, pred, zero_division=0)),
            "precision": float(precision_score(true, pred, zero_division=0)),
            "recall": float(recall_score(true, pred, zero_division=0)),
            "pred_rate": rate_pred, "true_rate": rate_true,
            "rate_gap": abs(rate_pred - rate_true)}


def mlflow_client():
    """Workspace-scoped managed MLflow; same shim as the other 101 code."""
    from pathlib import Path
    ws = os.environ["MLFLOW_WORKSPACE"]
    os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
    sa = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    if sa.exists():
        os.environ["MLFLOW_TRACKING_TOKEN"] = sa.read_text().strip()
    from mlflow.utils import rest_utils
    orig = rest_utils.http_request

    def shim(*a, **kw):
        # signature-agnostic: mlflow 3.4 calls http_request positionally on
        # some paths and keyword-only on others (learned live on Rome)
        h = dict(kw.pop("extra_headers", None) or {})
        h["X-MLFLOW-WORKSPACE"] = ws
        kw["extra_headers"] = h
        return orig(*a, **kw)

    rest_utils.http_request = shim
    from mlflow.store.tracking import rest_store
    rest_store.http_request = shim
    import mlflow
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment("5gprod-anomaly-sweep")
    return mlflow


ray.init()
print("cluster:", ray.cluster_resources(), flush=True)
alerts_raw = json.loads(fetch(f"{HF}/alerts.json"))
alerts = alerts_raw.get("alerts", alerts_raw if isinstance(alerts_raw, list) else [])
data = {nf: ray.put(fetch(f"{HF}/{nf}_metrics.csv")) for nf in NFS}
print("datasets staged in object store", flush=True)
results = ray.get([evaluate.remote(nf, c, data[nf], alerts)
                   for nf in NFS for c in GRID])

mlflow = mlflow_client()
with mlflow.start_run(run_name="ray-contamination-sweep") as run:
    mlflow.log_params({"grid": str(GRID), "nfs": str(NFS),
                       "objective": "min |pred_rate - labeled_rate|",
                       "compute": "RayJob on Kueue (agent-school-queue)"})
    best = {}
    for r in results:
        tag = f"{r['nf']}_c{str(r['contamination']).replace('.', '_')}"
        for k in ("f1", "precision", "recall", "pred_rate", "rate_gap"):
            mlflow.log_metric(f"{tag}_{k}", r[k])
        cur = best.get(r["nf"])
        if cur is None or r["rate_gap"] < cur["rate_gap"]:
            best[r["nf"]] = r
    for nf, r in best.items():
        mlflow.log_param(f"best_contamination_{nf}", r["contamination"])
        mlflow.log_metric(f"best_rate_gap_{nf}", r["rate_gap"])
        print(f"[best] {nf}: contamination={r['contamination']} "
              f"rate_gap={r['rate_gap']:.4f} f1={r['f1']:.3f}", flush=True)
    print("MLFLOW_RUN_ID", run.info.run_id, flush=True)
