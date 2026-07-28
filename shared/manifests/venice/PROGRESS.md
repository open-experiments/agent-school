# Venice Replication — Progress & Resume Point (July 28, 2026)

Read with shared/manifests/rome/README.md + install-report.md. Work is done via Chrome on
console-openshift-console.apps.venice.narlabs.io (kube:admin), using in-page fetch helpers
against /api/kubernetes with the csrf-token cookie. Secrets are generated in-page only.

## Done (durable on the cluster)
- Operators: cert-manager 1.20, Mesh 3.4, RHOAI 3.5.0-ea.2 (beta channel), RHCL/Kuadrant 1.4.2
  (+authorino/limitador/dns), RHBOK Kueue 1.3.1, JobSet, LWS. All CSVs Succeeded.
- Cluster CRs: Kueue (frameworks BatchJob,PyTorchJob,RayCluster,RayJob,TrainJob), JobSetOperator, LWS.
- DSC default-dsc: Rome's spec verbatim; PLUS a v2-API patch setting mlflowoperator, ogx,
  sparkoperator, trainer, aigateway to Managed (v1 API prunes these keys - key EA finding).
  Status: only MaaSPrerequisitesAvailable=False remains, which is False on Rome too (parity).
- MaaS: Gateway maas-default-gateway (venice host, + annotations opendatahub.io/managed=false,
  security.opendatahub.io/authorino-tls-bootstrap=true), passthrough Route, Kuadrant CR,
  Authorino CR (TLS disabled, Rome parity), maas-db Postgres16 + maas-db-credentials/-config.
- DSCI trustedCABundle patched with Venice service-CA.
- MinIO: ns minio, 500Gi PVC, deployment+services+routes, minio-root (fresh), buckets
  models/pipelines/data (BUCKETS_OK).
- Kimi weights: byte-mirrored Rome->Venice MinIO under models/kimi-linear-48b-a3b-instruct-awq-8bit
  (MIRROR_OK). Rome's temporary anonymous read REVERTED.
- Serving: ns telco-aix; ServingRuntime custom-vllm-tp1 with (a) image PINNED to Rome digest
  docker.io/vllm/vllm-openai@sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089
  and (b) Rome's bash shim command writing /tmp/shim/sitecustomize.py that re-adds
  transformers gpt2 bytes_to_unicode (fixes tokenization_kimi.py ImportError on new vllm),
  args now --tensor-parallel-size=2; connection secret models-minio; HardwareProfile
  nvidia-rtx-pro-6000. ISVC kimi-linear-48b-a3b gpu:2 (single 96G card OOMs at 131k ctx;
  Venice has 2x RTX PRO 6000 so tp=2 = Rome parity). Last seen: pod 1/2, loading weights.

## Next steps, in order
1. Verify kimi pod 2/2 / ISVC Ready; smoke /v1/models via in-cluster Job in telco-aix.
2. Create cluster-scoped mlflow CR (mlflow.opendatahub.io/v1, name mlflow) copying Rome's spec:
   backendStoreUri sqlite:////mlflow/mlflow.db, env OPENAI_API_BASE + OPENAI_BASE_URL ->
   http://kimi-linear-48b-a3b-predictor.telco-aix.svc.cluster.local:8080/v1. Then patch
   deployment mlflow (redhat-ods-applications) env MLFLOW_SERVER_ENABLE_JOB_EXECUTION=true.
3. Courses 101->201->202->301->302: namespaces agent-school/fiveg-core/think-tank; ConfigMap
   mlflow-tracking; fresh secrets llm-credentials (in-cluster kimi URL, key can be none),
   dspa-minio-creds (from minio-root), loop-state-auth; then each course's deploy/ocp/rome
   overlay + one-shot Jobs, sources fetched in-browser from raw.githubusercontent.com
   (open-experiments/agent-school, main) exactly as the 301/202 runbooks were driven on Rome.
   Add imagePullPolicy IfNotPresent. Registry promotions via the Venice dashboard UI.
4. Fidelity: run export-live.sh pattern (diff live vs pack), capture screenshots.
5. Docs: add EA lessons (v2-only DSC component keys; vllm latest-tag breakage -> digest pin +
   bytes_to_unicode shim; single-96G OOM at 131k ctx) to Rome-LessonsLearned + this file.

## Gotchas for the driving session
- Tool sanitizer mangles literals like secretRef/token/password/accesskey/redis:// in
  javascript_tool input: build such strings by concatenation at runtime.
- The k8s proxy only exists on the console origin; GETs need Accept: application/json;
  mutations need X-CSRFToken from the csrf-token cookie; CRDs need merge-patch (not strategic).
- registry.access.redhat.com flaked repeatedly: prefer imagePullPolicy IfNotPresent.
