"""Live smoke test against the served fraud model — real rows, real answer.

Pulls the first rows of the published billing dataset, applies the same
Plan_Type encoding the pipeline's preprocess step uses, and POSTs a
V2-protocol dataframe payload to the KServe predictor. Prints the served
fraud_probability / fraud_flag next to the true labels.

Gotcha (Rome convention, same as the Kimi predictor): the predictor
Service is HEADLESS — DNS resolves straight to the pod, so the port must
be explicit (:8080). Port 80 refuses.

Runs as deploy/ocp/rome/job-infer-smoke.yaml.
"""
import json
import os
import time
import urllib.request

import pandas as pd

URL = ("https://huggingface.co/datasets/fenar/revenue_assurance/"
       "resolve/main/telecom_revass_data.csv.xz")
df = pd.read_csv(URL, compression="xz").head(5)
y = df["Fraud"].tolist()
X = df.drop(columns=["Fraud"]).copy()
X["Plan_Type"] = (X["Plan_Type"] == "prepaid").astype(int)

inputs = []
for col in X.columns:
    v = X[col]
    if v.dtype.kind in "iu":
        dt, data = "INT64", [int(x) for x in v]
    elif v.dtype.kind == "f":
        dt, data = "FP64", [float(x) for x in v]
    else:
        dt, data = "BYTES", [str(x) for x in v]
    inputs.append({"name": col, "shape": [len(v), 1],
                   "datatype": dt, "data": data})

payload = json.dumps({"parameters": {"content_type": "pd"},
                      "inputs": inputs}).encode()
base = os.environ.get(
    "TARGET",
    "http://fraud-detector-predictor.agent-school.svc.cluster.local:8080")
req = urllib.request.Request(
    base + "/v2/models/fraud-detector/infer", data=payload,
    headers={"Content-Type": "application/json"})

resp = None
for attempt in range(4):
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        break
    except Exception as e:
        print("attempt", attempt, type(e).__name__, e)
        time.sleep(10)
if resp is None:
    raise SystemExit("all attempts failed")

out = {o["name"]: o["data"] for o in resp.get("outputs", [])}
print("INFER_OK")
print(json.dumps({"true_fraud_labels": y, "served": out}, indent=1))
