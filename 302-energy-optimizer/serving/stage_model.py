"""Stage the registered sustainability scorer from MLflow into MinIO.

Same bridge as 202 (KServe pulls object storage, not MLflow), made
parameterizable: MODEL_NAME/MODEL_VERSION env select the registered
version; the artifact tree lands at
s3://models/<MODEL_NAME>/<MODEL_VERSION>/.

Runs as deploy/ocp/rome/job-stage-scorer.yaml.
"""
import os
import pathlib

MODEL_NAME = os.environ.get("MODEL_NAME", "sustainability-energy-efficiency")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "1")

ws = os.environ["MLFLOW_WORKSPACE"]
os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
tok = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
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

import mlflow  # noqa: E402

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
local = mlflow.artifacts.download_artifacts(
    "models:/" + MODEL_NAME + "/" + MODEL_VERSION, dst_path="/tmp/model")
print("downloaded to", local)

import boto3  # noqa: E402

s3 = boto3.client(
    "s3", endpoint_url="http://minio.minio.svc.cluster.local:9000",
    aws_access_key_id=os.environ["AK"],
    aws_secret_access_key=os.environ["SK"], region_name="minio")
try:
    s3.create_bucket(Bucket="models")
except Exception as e:
    print("bucket:", type(e).__name__)

root = pathlib.Path(local)
n = 0
for p in sorted(root.rglob("*")):
    if p.is_file():
        key = MODEL_NAME + "/" + MODEL_VERSION + "/" + \
            str(p.relative_to(root))
        s3.upload_file(str(p), "models", key)
        n += 1
        print("up", key)
print("UPLOADED", n)
