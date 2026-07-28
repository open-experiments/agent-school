# Cluster tapes: time-snapshotted Rome and Venice

These files are complete, sanitized snapshots of the two Agent School reference clusters, captured live on July 28, 2026 while all five courses were deployed and proven. They exist so the course experience outlives the hardware: the interactive portal replays these tapes, and nothing a student sees in it is synthetic.

## Files

- `venice-tape-raw.json.gz`, `rome-tape-raw.json.gz`: the raw snapshots. Gunzip to get one JSON object per cluster.
- `MANIFEST.json`: a human-readable index of everything inside both tapes (every resource by namespace and kind, every captured log stream, every MLflow experiment and registry entry).
- `TAPE-SCHEMA.md`: the course-tape format the portal player consumes, and how it is derived from these raw snapshots.

## What was captured

Each raw tape contains four sections:

- `resources`: every Kubernetes object (spec + status, `managedFields` stripped) in the course namespaces `agent-school`, `fiveg-core`, `think-tank`, plus the serving namespace `telco-aix`, the registry namespace `rhoai-model-registries`, and the managed MLflow Deployment. Covers Deployments, Services, ConfigMaps (course source code, which is public), Jobs, InferenceServices, ServingRuntimes, RayJobs, Kueue objects and Workloads, the FeatureStore CR, the DSPA, Gateways, HTTPRoutes, AuthPolicies, RateLimitPolicies, RBAC, Argo Workflows, and the ModelRegistry CR.
- `cluster_scoped`: the platform CRs that define the environment (DataScienceCluster, DSCInitialization, MLflow CR, Kueue cluster objects, image registry config, gateway classes) plus the DSP workflow-controller RBAC fix.
- `logs`: every pod log still present at capture time, timestamped (75 streams on Venice, 42 on Rome), including the course one-shot Jobs, the agents, the MCP servers, and tails of the Kimi predictor and MLflow server.
- `rest`: dashboard-visible data captured over the platform APIs: all MLflow experiments with their runs, params, metrics and tags (41 runs on Venice, 137 on Rome), the workspace registered models, and the full model registry dump (9 registered models with versions and artifacts on each cluster).

## Sanitization

The capture excluded all Secret objects by construction, redacted env values whose names match credential patterns, stripped `last-applied` annotations, and the bundles were scanned before commit for hex/base64 credential material, bearer tokens, JWTs, and password-bearing URLs (zero findings; the one long base64 value present is the feast client config, which contains no auth). Cluster hostnames remain, deliberately: the tapes document real deployments.

## Reproduce a capture

The capture runs entirely in a logged-in browser tab against the console's Kubernetes API proxy (no cluster-side install), plus one in-cluster Job that exports MLflow data under an authorized ServiceAccount. The pattern is documented in `shared/manifests/venice/PROGRESS.md` (method notes). Re-run it any time the clusters change and you want a fresh tape.
