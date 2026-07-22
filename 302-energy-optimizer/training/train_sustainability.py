"""Train + register the sustainability (energy-efficiency) scorer.

Faithful, pipeline-run reproduction of the Telco-AIX `sustainability`
notebook (energy_cons_prediction.ipynb): StandardScaler +
LinearRegression over 11 network KPIs, target `Fault Occurrence Rate
(%)`, with energy efficiency reported as `100 - predicted fault rate`
— the source experiment's own definition. Data is the experiment's
published 100K-row 5G netops dataset, pulled from the Telco-AIX repo.

Logs the run to MLflow (experiment `302-energy-optimizer`, workspace
agent-school) and registers `sustainability-energy-efficiency` — the
version the 302 agent's score step consumes from KServe.

Runs as deploy/ocp/rome/job-train-scorer.yaml.
"""
import os
from pathlib import Path

DATA_URL = ("https://raw.githubusercontent.com/open-experiments/Telco-AIX/"
            "main/sustainability/data/5G_netops_data_100K.csv.xz")
FEATURES = ["Cell Availability (%)", "MTTR (hours)", "Throughput (Mbps)",
            "Latency (ms)", "Packet Loss Rate (%)", "Call Drop Rate (%)",
            "Handover Success Rate (%)", "Alarm Count",
            "Critical Alarm Count", "Temperature (°C)", "Humidity (%)"]
TARGET = "Fault Occurrence Rate (%)"

# ---- workspace-scoped MLflow shims (same as every Rome workload) ----
ws = os.environ["MLFLOW_WORKSPACE"]
os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
tok = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
if tok.exists():
    os.environ["MLFLOW_TRACKING_TOKEN"] = tok.read_text().strip()
from mlflow.utils import rest_utils  # noqa: E402

orig = rest_utils.http_request


def shim(*a, **kw):
    h = dict(kw.pop("extra_headers", None) or {})
    h["X-MLFLOW-WORKSPACE"] = ws
    kw["extra_headers"] = h
    return orig(*a, **kw)


rest_utils.http_request = shim
from mlflow.store.tracking import rest_store  # noqa: E402

rest_store.http_request = shim
import requests as rq  # noqa: E402

orig_req = rq.Session.request


def req(self, method, url, **kw):
    if "mlflow" in url:
        h = kw.get("headers") or {}
        h["X-MLFLOW-WORKSPACE"] = ws
        kw["headers"] = h
    return orig_req(self, method, url, **kw)


rq.Session.request = req

import joblib  # noqa: E402
import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.linear_model import LinearRegression  # noqa: E402
from sklearn.metrics import (mean_absolute_error,  # noqa: E402
                             mean_squared_error, r2_score)
from sklearn.model_selection import train_test_split  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment("302-energy-optimizer")

df = pd.read_csv(DATA_URL, compression="xz")
X, y = df[FEATURES], df[TARGET]
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2,
                                          random_state=42)
scaler = StandardScaler().fit(X_tr)
model = LinearRegression().fit(scaler.transform(X_tr), y_tr)
pred = model.predict(scaler.transform(X_te))
mae = mean_absolute_error(y_te, pred)
rmse = mean_squared_error(y_te, pred) ** 0.5
r2 = r2_score(y_te, pred)
print(f"[eval] mae={mae:.3f} rmse={rmse:.3f} r2={r2:.4f}")

bundle_path = "/tmp/sustainability_bundle.joblib"
joblib.dump({"scaler": scaler, "model": model, "features": FEATURES},
            bundle_path)


class SustainabilityModel(mlflow.pyfunc.PythonModel):
    """Scores network KPI rows: predicted fault rate and the source
    experiment's energy-efficiency definition (100 - fault rate)."""

    def load_context(self, context):
        import joblib as _joblib
        b = _joblib.load(context.artifacts["bundle"])
        self.scaler, self.model = b["scaler"], b["model"]
        self.features = b["features"]

    def predict(self, context, model_input, params=None):
        import pandas as _pd
        Xs = self.scaler.transform(model_input[self.features])
        fault = self.model.predict(Xs)
        return _pd.DataFrame({
            "predicted_fault_rate": fault,
            "energy_efficiency": 100.0 - fault})


with mlflow.start_run(run_name="sustainability-lr") as run:
    mlflow.log_params({
        "algorithm": "StandardScaler+LinearRegression",
        "dataset": DATA_URL, "rows": len(df),
        "target": TARGET,
        "efficiency_definition": "100 - predicted_fault_rate "
                                 "(source notebook's definition)"})
    mlflow.log_metrics({"mae": mae, "rmse": rmse, "r2": r2})
    mlflow.pyfunc.log_model(
        name="sustainability-scorer",
        python_model=SustainabilityModel(),
        artifacts={"bundle": bundle_path},
        registered_model_name="sustainability-energy-efficiency")
    print("[register] run=" + run.info.run_id +
          " -> sustainability-energy-efficiency")
