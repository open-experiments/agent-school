# RHOAI 3.5 EA2 Installation Report — SNO "rome"

**Date:** July 19–20, 2026 · **Cluster:** OpenShift 4.22.4, single-node (`54-bf-64-90-73-fc`, 88 cores / 251.5 GiB) · **GPUs:** 2× NVIDIA GeForce RTX 4090 D · **Console:** console-openshift-console.apps.rome.narlabs.io

## Objective

Install Red Hat OpenShift AI 3.5 Early Access 2 with the full AI stack — model catalog, model registry, OGX (formerly Llama Stack), AI Gateway, Models-as-a-Service, KServe, pipelines, distributed training — plus MinIO object storage, on an SNO cluster that already had NFD, LVM Storage, and the NVIDIA GPU Operator installed.

## What was done

**Pre-flight verification.** Confirmed OCP 4.22.4 healthy, NVIDIA GPU Operator 26.3.3 with both 4090 Ds discovered (`nvidia.com/gpu.count=2`), NFD 4.22.0, and LVM Storage with `lvms-vg1` as default StorageClass (~47 TiB free across /dev/sda-sdc).

**Operator installs.** cert-manager 1.20.0, Service Mesh 3.4.0, and the RHOAI operator 3.5.0-ea.2 (`rhods-operator`, **beta** channel, automatic approval). During reconciliation four more dependencies proved necessary and were installed: Red Hat Connectivity Link 1.4.1 (Kuadrant, with Authorino/Limitador/DNS operators), Red Hat build of Kueue 1.3.1, JobSet operator 1.0.0, and LeaderWorkerSet operator 1.0.0.

**DataScienceCluster.** Created `default-dsc` (v2 API) with all components Managed: dashboard, workbenches, aipipelines, kserve (+ modelsAsService), modelregistry, ogx, aigateway, trustyai, feastoperator, mlflowoperator, ray, trainer, trainingoperator, sparkoperator; kueue Unmanaged (delegated to RHBOK). Supporting resources created along the way: GatewayClass `openshift-default`, Gateway `maas-default-gateway` in openshift-ingress (TLS via default router cert), passthrough Route `maas.apps.rome.narlabs.io`, Kuadrant CR, Authorino with service-CA TLS, PostgreSQL 16 + `maas-db-config` secret for MaaS, and `JobSetOperator`/`LeaderWorkerSetOperator`/`Kueue` cluster CRs. Final state: **DSC Ready, zero failing conditions**.

**MinIO.** Deployed in namespace `minio` with a 500Gi PVC on lvms-vg1. S3 API at `minio-api.apps.rome.narlabs.io`, console at `minio-console.apps.rome.narlabs.io` (user `minio-admin`, password in secret `minio/minio-root`). Buckets `models`, `pipelines`, `data` created and verified. A `sandbox` data science project carries three S3 connection secrets wired to those buckets.

**GPU enablement + smoke test.** Hardware profile `nvidia-rtx-4090d` (1–2 GPUs, 1–64 CPU, 2–128Gi memory) created for the dashboard. A test job requesting both GPUs ran `nvidia-smi` successfully: driver 580.126.20, CUDA 13.0, 49,140 MiB VRAM per card.

## Failure points encountered (all resolved)

1. **Wrong channel default.** The `fast` channel head on the 4.22 catalog is RHOAI **2.25.8** (2.x line); 3.5 EA2 lives on **beta**. The brief 2.25.8 install was removed cleanly (subscription, CSV, stuck `default-dsci` finalizer) before re-subscribing.
2. **kueue Managed rejected** by the v2 admission webhook — 3.5 requires Unmanaged + external RHBOK operator.
3. **llamastackoperator deprecated** in EA2 — OGX refused to reconcile until it was set Removed.
4. **JobSet/LWS operators failed initially** — they only support OwnNamespace install mode (OperatorGroup fix + subscription recreate), and each needs its own `*Operator/cluster` CR before the controller actually deploys.
5. **MaaS prerequisites** — required a Gateway, Kuadrant/Authorino (with listener TLS via service-CA annotation), and a Postgres connection secret, none of which the operator creates itself.
6. **No LoadBalancer on SNO bare metal** — the MaaS gateway service stays `Pending`; worked around with a TLS-passthrough Route through the default router.
7. **MinIO bucket job** — `mc` failed under OpenShift's random-UID SCC until `HOME=/tmp` was set.

## What's left / recommendations

Nothing is blocking: the platform is installed, Ready, and GPU-verified. Sensible next steps, in order of value: upload a model to the `models` bucket and stand up a vLLM `LLMInferenceService` to exercise KServe + the gateway path end to end; spin up a CUDA workbench on the `nvidia-rtx-4090d` profile; configure a pipeline server in `sandbox` against the `pipelines` connection; and exercise OGX's Responses API for the Agent-as-a-Service pattern. Housekeeping items: the cluster still runs on kubeadmin (configure an OAuth IdP), the trial SLA shows 59 days remaining, MaaS `Programmed`/`DNSReady` conditions on the Gateway remain False cosmetically (traffic flows via the passthrough route; MetalLB would make it native), and the beta-channel subscription will auto-upgrade to future EA builds — pin it if you want EA2 frozen. The `gpu-smoke-test` job in `sandbox` and the Chrome extension's missing permission for `rh-ai.apps.rome.narlabs.io` (needed if you want me driving the RHOAI UI) are minor leftovers.
