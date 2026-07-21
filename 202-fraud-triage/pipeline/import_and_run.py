"""Compile, upload, and run the 202 fraud pipeline from inside the cluster.

Runs as deploy/ocp/rome/job-import-pipeline.yaml (ConfigMap-mounted at
/src). Talks to the pipeline server through kube-rbac-proxy on 8443 using
the pod's ServiceAccount token and the OpenShift service CA — the plain
8888 port is NetworkPolicy-restricted (Rome, RHOAI 3.5 EA2 finding).

Idempotent-ish: if the pipeline already exists, uploads a new version
instead; runs are numbered by the RUN_NAME env (default fraud-brf-run-1).
"""
import os
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                "kfp==2.17.0"], check=True)

import importlib.util

import kfp
from kfp import compiler

spec = importlib.util.spec_from_file_location(
    "ftp", "/src/fraud_train_pipeline.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
compiler.Compiler().compile(m.fraud_train_pipeline, "/tmp/p.yaml")
print("compiled ok", flush=True)

tok = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read().strip()
ca = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
c = kfp.Client(
    host="https://ds-pipeline-dspa.agent-school.svc.cluster.local:8443",
    existing_token=tok, ssl_ca_cert=ca)

NAME = "fraud-brf-training"
existing = [p for p in c.list_pipelines(page_size=50).pipelines or []
            if p.display_name == NAME]
if existing:
    pid = existing[0].pipeline_id
    v = c.upload_pipeline_version(
        "/tmp/p.yaml", pipeline_id=pid,
        pipeline_version_name=os.environ.get("VERSION_NAME", "v-update"))
    vid = v.pipeline_version_id
else:
    p = c.upload_pipeline(
        "/tmp/p.yaml", pipeline_name=NAME,
        description="202 fraud-triage: revenue_assurance -> preprocess -> "
                    "BRF -> evaluate -> register (MLflow)")
    pid = p.pipeline_id
    vid = c.list_pipeline_versions(pid).pipeline_versions[0].pipeline_version_id
print("pipeline", pid, "version", vid, flush=True)

exps = [e for e in c.list_experiments(page_size=50).experiments or []
        if e.display_name == "fraud-brf"]
eid = exps[0].experiment_id if exps else c.create_experiment("fraud-brf").experiment_id
run = c.run_pipeline(experiment_id=eid,
                     job_name=os.environ.get("RUN_NAME", "fraud-brf-run-1"),
                     pipeline_id=pid, version_id=vid)
print("RUN_ID", run.run_id, flush=True)
