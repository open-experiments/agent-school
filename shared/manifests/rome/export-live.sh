#!/usr/bin/env bash
# Re-export the hand-tuned live resources from a running Agent School
# cluster (Rome) so they can be diffed against the manifests in this
# directory before replicating elsewhere (Venice). Run with cluster-admin.
set -euo pipefail
OUT="${1:-./live-export}"
mkdir -p "$OUT"

x() { oc get "$1" -n "$2" "$3" -o yaml > "$OUT/$3.yaml" && echo "exported $3"; }

# Model serving (telco-aix)
x servingruntime      telco-aix   custom-vllm-tp2
x servingruntime      telco-aix   custom-vllm-runtime
x inferenceservice    telco-aix   kimi-linear-48b-a3b
x inferenceservice    telco-aix   diffusiongemma-26b-a4b-it-fp8

# MinIO
oc get deploy,svc,route,pvc -n minio -o yaml > "$OUT/minio-all.yaml"

# Feature store + pipelines (agent-school)
x featurestore                       agent-school fivegprod
x datasciencepipelinesapplication    agent-school dspa

# Cluster-level trust + MLflow tweaks
oc get dscinitialization default-dsci -o yaml > "$OUT/dsci.yaml"
oc get deploy mlflow -n redhat-ods-applications -o yaml > "$OUT/mlflow-deploy.yaml"
oc get datasciencecluster default-dsc -o yaml > "$OUT/dsc.yaml"

echo "done -> $OUT/"
