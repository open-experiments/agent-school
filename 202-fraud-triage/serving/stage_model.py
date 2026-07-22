"""Stage the registered fraud model out of managed MLflow into MinIO.

KServe's storage initializer pulls from object storage, not from MLflow —
so serving needs the registered version's artifact tree copied to a
bucket first. This is the bridge: download `models:/revassurance-fraud-brf/1`
through the workspace-scoped tracking server (same header/token shims as
every other Rome workload), then upload the tree to
s3://models/revassurance-fraud-brf/1/ on the cluster MinIO.

Runs as deploy/ocp/rome/job-stage-model.yaml. Idempotent: re-running
overwrites the same keys.
"""
import os
import pathlib

ws = os.environ["MLFLOW_WORKSPACE"]
os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
tok = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
if tok.exists():
    os.environ["MLFLOW_TRACKING_TOKEN"] = tok.read_text().strip()

from mlflow.utils import rest_utils  # noqa: E402

orig = rest_utils.http_request


def shim(*a, **kw):
    # signature-agnostic: mlflow 3.4 calls http_request positionally on
    # some paths and keyword-only on others (EA2 finding, learned live)
    h = dict(kw.pop("extra_headers", None) or {})
    h["X-MLFLOW-WORKSPACE"] = ws
    kw["extra_headers"] = h
    return orig(*a, **kw)


rest_utils.http_request = shim
from mlflow.store.tracking import rest_store  # noqa: E402

rest_store.http_request = shim

# artifact downloads bypass rest_utils -> inject the workspace header at
# the requests level too (same EA2 finding as the pipeline's register step)
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
    "models:/revassurance-fraud-brf/1", dst_path="/tmp/model")
print("downloaded to", local)

import boto3  # noqa: E402

s3 = boto3.client(
    "s3", endpoint_url="http://minio.minio.svc.cluster.local:9000",
    aws_access_key_id=os.environ["AK"],
    aws_secret_access_key=os.environ["SK"], region_name="minio")
try:
    s3.create_bucket(Bucket="models")
    print("bucket models created")
except Exception as e:
    print("bucket:", type(e).__name__)

root = pathlib.Path(local)
n = 0
for p in sorted(root.rglob("*")):
    if p.is_file():
        key = "revassurance-fraud-brf/1/" + str(p.relative_to(root))
        s3.upload_file(str(p), "models", key)
        n += 1
        print("up", key)
print("UPLOADED", n)
