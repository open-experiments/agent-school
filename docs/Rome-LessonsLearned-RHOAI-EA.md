# Rome — RHOAI 3.5 EA Lessons Learned

Environment of record: **Rome** — Red Hat OpenShift AI `3.5.0-ea.2` on OpenShift `4.22` (Single-Node OpenShift). Supporting operators: `authorino-operator v1.4.1`, `servicemeshoperator3 v3.4.0` (OSSM 3.4 / Istio), Kuadrant via **Red Hat Connectivity Link** (`registry.redhat.io/rhcl-1/...`). Console: `console-openshift-console.apps.rome.narlabs.io`.

This document captures findings from building the `agent-school` course stack (101 / 201 / 202 / 301 / 302) against a pre-GA RHOAI build, so a future larger-GPU cluster ("Venice") can be stood up from the repo without re-hitting the same walls. Each entry states the context, the observed symptom, the verified root cause, and the resolution actually applied on Rome.

---

## Headline conclusion: secure agentic comms require a full Istio service mesh

Building a governed tool boundary for the closed-loop NetOps course (301, Track 4) surfaced the single most important architectural lesson of this EA work:

> **To secure agentic communication end to end — East-West (agent ↔ MCP gateway ↔ Authorino/Limitador ↔ MCP tool server) and North-South (edge ingress into the agents) — a full Istio service mesh must be in place, with the Kuadrant policy-enforcement components enrolled as mesh workloads (sidecars). The RHOAI-managed gateway Istio (`data-science-gateway-class`) on its own is not sufficient to secure the control-plane hops that Kuadrant depends on.**

Why this is inevitable rather than optional: Kuadrant enforces WHO/HOW-OFTEN at the gateway by having the gateway's Envoy call out to Authorino (authN/authZ) and Limitador (rate limiting) over gRPC on every request. Those calls are the security-critical East-West hops of the agentic control plane. Making them mutually authenticated and encrypted is a first-class mesh concern, and Kuadrant's supported mechanism for it (`Kuadrant` CR `spec.mtls`) is built on Istio `PeerAuthentication` + sidecar mTLS. Without a full mesh and enrolled components, you are forced to choose between *consistent plaintext* (works, but the control-plane hop is unencrypted) or a *half-configured TLS state* that fails closed. There is no secure middle ground that avoids the mesh.

The detailed finding that establishes this conclusion is EA-1 below.

---

## EA-1 — Kuadrant ext_authz TLS mismatch → blanket HTTP 500 at the gateway

**Course / context.** 301 Track 4: a dedicated Kuadrant-governed `Gateway` (`netops-gateway`, class `data-science-gateway-class`) fronts the autonet-playbook MCP server. An `AuthPolicy` (Authorino) restricts actuation to the `execution-agent` identity; a `RateLimitPolicy` (Limitador) caps it. The gateway routes `/mcp` to the MCP server.

**Symptom.** Every request through the gateway returned a bare `HTTP 500` with body `Internal Server Error.`, `content-type: text/plain`, `connection: close`, and **no `server: envoy` header** — including an unauthenticated (no-token) request that should have been a clean `401`. Control-plane status was green: `AuthPolicy` and `RateLimitPolicy` both `Accepted=True, Enforced=True`; the HTTPRoute attached; the backend pod Ready.

**Root cause (verified).** The Kuadrant wasm-shim on the gateway loaded and ran correctly (`wasm.remote_load_fetch_successes: 1`, `wasmcustom.kuadrant.hits > 0`) but reported `wasmcustom.kuadrant.errors`. Its per-request gRPC calls split cleanly:

- `ratelimit → Limitador` resolved to a **pod IP** (headless service), connected, succeeded (`failureMode: allow` anyway).
- `auth → Authorino` resolved to the Service **ClusterIP VIP** `:50051`, TCP-connected (`cx_connect_fail: 0`) but the request errored (`rq_error: 1`).

Authorino's startup log was decisive: `starting grpc auth service port=50051 tls=true` — Authorino served its ext_authz listener **with TLS** (an OpenShift service-serving cert, secret `authorino-server-cert`), but the auth cluster Kuadrant injected into the gateway's Envoy (`kuadrant-auth-service`) was **plaintext** — `http2_protocol_options: {}` with **no `transport_socket`**. Envoy spoke plaintext HTTP/2 into a TLS listener, Authorino reset the stream, and because the wasm-shim's auth action is `failureMode: deny`, every request failed closed with the shim's default `500 Internal Server Error.` — before authN was ever evaluated.

**Diagnosis method (reusable).** Envoy admin (`:15000`) is only reachable inside the gateway pod and the console proxy cannot exec (no SPDY). An **ephemeral debug container** sharing the pod network namespace was used to curl the admin API:

```sh
# attach an ephemeral container to the gateway pod, then from it:
curl -s localhost:15000/clusters | grep kuadrant-auth-service      # endpoint = VIP vs pod IP, rq_error
curl -s localhost:15000/stats    | grep -i 'wasmcustom.kuadrant'   # hits / allowed / denied / errors
curl -s localhost:15000/config_dump | grep -A120 '"kuadrant-auth-service"'  # transport_socket present?
```

**What did *not* work (recorded so we don't retry it).** A supplemental `EnvoyFilter` that `MERGE`-patches a TLS `transport_socket` onto `kuadrant-auth-service` does **not** reliably apply: Istio will not dependably MERGE onto a cluster that another EnvoyFilter (Kuadrant's) added via `ADD`. Priorities were equal and creation order was correct, yet the transport never landed (`config_dump` showed the cluster still plaintext, no `ssl.*` stats). Aligning at the Authorino side is the workable lever.

**Resolution applied on Rome (interim, consistent-plaintext).** Authorino ext_authz has exactly one consumer on this cluster — verified: `oc get authpolicy -A` returns only `agent-school/mcp-playbook-authn` — so disabling its listener TLS has no other blast radius:

```sh
oc patch authorino authorino -n kuadrant-system --type merge \
  -p '{"spec":{"listener":{"tls":{"enabled":false}}}}'
# verify:
oc logs deploy/authorino -n kuadrant-system | grep 'grpc auth service'
#   -> "starting grpc auth service port=50051 tls=false"
```

Both sides are now plaintext and consistent; the governed path proves out end to end (401 no-token, 403 wrong identity, 200 execution-agent, real `restart_smf` actuation, 429 after the rate cap).

**The supported encrypted path (and why it needs the mesh).** The `Kuadrant` CR exposes a real, supported knob (confirmed in the on-cluster CRD `kuadrants.kuadrant.io`, `v1beta1`):

```yaml
apiVersion: kuadrant.io/v1beta1
kind: Kuadrant
metadata: { name: kuadrant }
spec:
  mtls:
    enable: true        # wire mTLS gateway <-> Kuadrant components
    authorino: true      # per-component opt-out toggles
    limitador: true
```

Per the CRD description and the Kuadrant docs, enabling it makes the operator create a **STRICT Istio `PeerAuthentication`** and configure the gateway↔component transport as mTLS on both sides — the exact consistency mechanism, no hand-rolled EnvoyFilter. **But its prerequisites are not met on Rome:** the docs require a *full* Istio service mesh (not the OpenShift Ingress-managed Istio), the Istio CNI agent on each node, and mTLS terminated by **sidecars** on the components. On Rome, `authorino` and `limitador-limitador` run as single-container pods with **no Istio sidecar**; `kuadrant-system` has **no `istio-injection` label**; no `PeerAuthentication` exists. Flipping `spec.mtls.enable: true` today would create a STRICT policy with nothing to enforce it and point the gateway at mesh-mTLS toward sidecar-less pods → the handshake fails → back to 500s. Also note Authorino's pre-existing TLS was an **OpenShift service-serving cert**, a *different* mechanism than the sidecar/PeerAuthentication mTLS `spec.mtls` drives — that mismatch is the underlying root of the inconsistency.

**Action for the secure build (Venice / hardening).** Stand up a full Istio service mesh, enroll the Kuadrant components (sidecar injection into `kuadrant-system` for Authorino and Limitador), then set `Kuadrant` `spec.mtls.enable: true`. This secures the East-West control-plane hops; combine with edge (N-S) gateway TLS for the full picture. Treat this as a deliberate mesh-hardening workstream, not a per-course toggle.

**No upgrade shortcut.** This is a configuration inconsistency, not an operator code bug an upgrade patches. Rome already runs versions *ahead of* the release channels: RHOAI `3.5.0-ea.2` (beta) is newer than `fast → 2.25.8`, `fast-3.x → 3.3.1`, and `stable-3.x → 3.4.2`; `authorino-operator v1.4.1` is the newest in its `stable` channel. There is nothing newer to move to.

**Refs.**
- Kuadrant mTLS configuration: https://docs.kuadrant.io/latest/kuadrant-operator/doc/install/mtls-configuration/
- Kuadrant auth overview: https://docs.kuadrant.io/dev/kuadrant-operator/doc/overviews/auth/
- Repo: `301-closed-loop-netops/deploy/ocp/rome/mcp-gateway-policies.yaml` (prerequisite block + audiences), `netops-gateway.yaml`, `agents/mcp-playbook/gw_smoke.py` (the allow/deny/rate-limit smoke).

---

## EA-2 — AuthPolicy `kubernetesTokenReview` needs explicit `audiences`

**Context.** Same Track 4 path, after EA-1 was resolved.

**Symptom.** With the transport fixed, valid ServiceAccount tokens were still rejected: no-token → `401` (correct), but a *valid* `execution-agent` token also → `401` (expected `200`), and a valid-but-wrong identity → `401` at authN rather than `403` at authZ.

**Root cause.** A pod's default projected token (`/var/run/secrets/kubernetes.io/serviceaccount/token`) is minted with `aud: ["https://kubernetes.default.svc"]`. With no `audiences` set, Authorino defaults the TokenReview audience to the AuthConfig's internal scope hash, which no real token carries → `authenticated: false` → `401`.

**Resolution.** Set the audience the mounted token actually carries, so the standard token authenticates with no projected-audience volume:

```yaml
rules:
  authentication:
    "sa-token":
      kubernetesTokenReview:
        audiences:
          - https://kubernetes.default.svc
```

After this: wrong identity → `403` (authN passes, authZ denies — semantically exact), execution-agent → `200`. Captured in `mcp-gateway-policies.yaml`.

---

## EA-3 — The shared `data-science-gateway` rejects cross-namespace routes

**Context.** Attaching agent-school HTTPRoutes to the platform gateway.

**Symptom / cause.** The shared `data-science-gateway` only accepts routes from `openshift-ingress` / `redhat-ods-applications` (a hard namespace selector). Routes from `agent-school` are refused.

**Resolution.** Give agent-school its own `Gateway` on the same gateway class (`data-science-gateway-class`). On SNO the gateway's LoadBalancer Service gets no external address (`Gateway` shows `AddressNotAssigned`, `Programmed=False`) — this is fine for the closed loop, whose calls are East-West over the gateway's ClusterIP. Ref: `netops-gateway.yaml`.

---

## EA-4 — Kueue `frameworks` must exclude `Deployment`/`Pod`/`StatefulSet`

**Context.** Enabling RHBOK Kueue for GPU/job gating (101, 302).

**Symptom.** Including `Deployment`/`Pod`/`StatefulSet` in the Kueue-managed frameworks broke operator-owned Deployments — e.g. `mariadb-dspa` was denied admission (the injected, immutable `kueue.x-k8s.io/queue-name` label collided with the operator's reconcile).

**Resolution.** Restrict the frameworks list to workload kinds the courses actually queue: `["BatchJob", "PyTorchJob", "RayCluster", "RayJob", "TrainJob"]`. DSPA and other operator Deployments then reconcile normally.

---

## EA-5 — Custom MLServer runtime crashes with parallel workers

**Context.** Serving MLflow-staged models on a custom MLServer `ServingRuntime` (202 fraud, 302 scorer).

**Symptom.** The runtime crashed on startup with an event-loop error ("no current event loop").

**Resolution.** Set `MLSERVER_PARALLEL_WORKERS=0` in the runtime env — run in-process, no worker pool.

---

## EA-6 — KServe headless predictor Service needs an explicit `:8080`

**Context.** InferenceService predictor reachability (202).

**Symptom.** Calls to the predictor were refused on port 80.

**Resolution.** The headless predictor Service must publish `:8080` explicitly; without it the default port mapping leaves the model port unreachable.

---

## EA-7 — Llama Stack collides with Kubernetes service-link env

**Context.** Running Llama Stack (`0.2.12`) as a Deployment pointing at the cluster vLLM (302).

**Symptom.** Startup failed with `ValueError: invalid literal for int(): 'tcp://172.30.x.x:8321'` — a Kubernetes service-link env var (`LLAMA_STACK_PORT=tcp://...`) was parsed by the server as its own config.

**Resolution.** Set `enableServiceLinks: false` on the pod spec so Kubernetes does not inject the colliding service-link env.

---

## EA-8 — kubelet ConfigMap mount cache TTL serves stale code

**Context.** Iterating on agent/sim code delivered via ConfigMap-mounted files.

**Symptom.** A freshly launched pod occasionally mounted the *previous* ConfigMap content when started very soon (< ~100 s) after the ConfigMap was updated.

**Resolution.** After updating a mounted ConfigMap, wait out the kubelet mount-cache TTL (~1–2 min) before launching pods that depend on the new content, or roll a pod-template annotation to force a fresh mount. Relevant to any "edit CM → launch Job" loop.

---

## EA-9 — Managed MLflow is workspace-scoped (request-level shim required)

**Context.** All courses log runs/metrics/artifacts to the RHOAI-managed MLflow.

**Symptom / cause.** The managed MLflow is workspace-scoped: requests need an `X-MLFLOW-WORKSPACE` header plus a ServiceAccount bearer token (Authorino does a SubjectAccessReview). The stock `mlflow` client sends neither, so tracking and especially artifact upload fail (e.g. `log_dict` → `400`).

**Resolution.** Before `import mlflow`, patch `mlflow.utils.rest_utils.http_request`, `mlflow.store.tracking.rest_store.http_request`, and `requests.Session.request` to inject the workspace header and token (the last is needed for artifact-store uploads, which bypass the first two). This shim is shared across the courses.

---

## EA-10 — Version pins that matter

Pre-GA and fast-moving SDKs required exact pins to keep the agent harnesses stable:

- `a2a-sdk == 0.3.22` — the `1.x` line reshuffled the server API (`A2AStarletteApplication`, `DefaultRequestHandler`, `AgentExecutor`); newer releases break the agent-card / executor wiring used here.
- `mcp == 1.28.1` — `FastMCP(..., stateless_http=True)` + `streamable-http` transport; client `streamablehttp_client` + `ClientSession`.
- `llama-stack == 0.2.12` / `llama-stack-client == 0.2.12` — `remote::vllm` provider, `Agent(...)` keyword form with `enable_session_persistence=True`. **Also install `fire` and `termcolor`**: `llama_stack_client.lib.agents.agent` imports `lib.tools.mcp_oauth`, which does `import fire` at module load — omit them and the agent pod crashes with `ModuleNotFoundError: No module named 'fire'` before serving. (OGX is the article's name for this Llama Stack agent-framework layer.)

---

## EA-11 — Kuadrant RateLimitPolicy counts gateway requests, not tool calls

**Context.** 301 Track 4c: after rewiring the Execution agent to actuate through the MCP gateway (it now holds no fiveg-core RBAC and calls the MCP playbook server over the governed path), the full loop was reproven.

**Symptom.** A three-step remediation plan actuated the first two playbooks (`rebalance_upf`, `restart_smf`) with `rc=0` but the third (`scale_amf`) failed instantly (`rc=-1`, `0.0s`) — a client-side gateway error, not an Ansible failure. The MCP server log showed it only ever received the first two tool calls.

**Root cause.** The `RateLimitPolicy` (Limitador) counts **gateway HTTP requests**, and MCP streamable-HTTP spends **~3–4 requests per tool call** (the `initialize` handshake, then `list`/`call`). So a 3-step plan is ~9–12 gateway requests, and the original `8/60s` cap — chosen to make the burst smoke visibly throttle — tripped mid-plan. The third actuation was `429`'d at the gateway before it reached the server.

**Resolution.** Raised the cap to `30/60s`, which comfortably fits a full multi-step incident (~4–5 playbooks × ~3–4 requests) while still stopping a runaway/looping agent from sustained hammering. The loop then completed with all three actuations `rc=0` and the rollback drill (`amf 3→2`) also flowing through the gateway. Captured in `mcp-gateway-policies.yaml`; the `gw_smoke.py` burst was bumped to 40 so the rate-limit proof still shows throttling against the higher cap.

**Design note (future cleanup).** The cap could be made to read as "N actuations/min" rather than "N requests/min" by having the Execution agent reuse a single MCP session across a plan's steps (one `initialize` + N `call_tool`, instead of a fresh session per playbook). Deferred; the request-based cap with headroom is correct and documented for now.

**Governance outcome (Track 4c, proven live).** With this in place, the closed loop runs entirely through the governed path: Execution's `execution-agent → nf-actuator` RoleBinding is deleted, so `playbook-actuator` (the MCP server) is the *sole* holder of fiveg-core actuation RBAC. Execution actuates only by presenting its own SA token to the gateway; Authorino proves the identity, Limitador caps the rate, the MCP server enforces the catalog and does the real work. Verified end to end: full loop `all_ok=True` (3 playbooks `rc=0`) and the rollback safety arm (`amf 3→2`) both actuated with Execution holding zero direct access. Refs: `agents/execution/agent.py`, `deploy/ocp/rome/execution.yaml`, `execution-rbac.yaml`, `agents/execution/smoke_loop.py`, `agents/validation/smoke_validate.py`.

---

## EA-12 — Quant+qual co-decision: a full-co-decider LLM judge WILL override with flawed rationale — audit it

**Context.** 302 "quant+qual": the classic sustainability regressor (calibrated, blind-and-mute) and an OGX (Llama Stack / Kimi) GenAI judge (context-aware, uncalibrated) co-decide each cell-sleep proposal. Both signals reach the classic model ONLY through the Kuadrant gateway's scorer MCP tool (`/score`) — East-West governance applied to *evaluation*, mirroring 301's actuation gate. Arbiter design: judge is a **full co-decider** (its accept/reject stands), with exactly one non-negotiable rail (`qos_dropped_pct > HARD_QOS_FLOOR_PCT` rejects regardless), and every judge-vs-quant disagreement recorded as an audited override.

**What ran (live on Rome).**
- *Consensus accept* (episode `e9e3435c55`): 3 cells asleep 00:00–06:00 → sim 7.67% savings, 0% QoS drop → governed score eff 68.33 → judge `accept` (0.85) citing the exact numbers and flagging real risks (coverage gaps, emergency services) → `final=accept`, no override.
- *Live audited override* (episode `2c9760ce72`, thresholds tightened to savings≥50%): quant gate FAILED the same proposal (7.67% < 50%), judge `accept` (0.85) → `override=true`, `final=accept`, override note stored verbatim in MLflow.

**The finding.** The override's stored rationale reads *"7.67% savings exceeds the 50% threshold…"* — *factually false* (the judge hallucinated its justification while reaching a defensible-in-context decision). This is the precise failure mode of giving an LLM full co-decider authority: it can overturn a calibrated gate on reasoning that doesn't hold. The mitigations that made this safe-to-operate, verified: (1) the hard QoS rail beats even a dual-accept (arbiter truth-table unit-checked: `(quant_ok=True, judge=accept, qos=2.5) → reject`); (2) every override is recorded with the judge's verbatim rationale, so a human reviews exactly what the model claimed; (3) judge-unreachable falls back to the quant gate alone, honestly recorded. Recommendation for production postures: veto-only (judge can only be more conservative) unless there is an explicit reason for full co-decision; if full co-decision, LLM-as-judge evaluation (MLflow GenAI eval) should include a groundedness/faithfulness scorer that catches exactly this rationale-vs-numbers mismatch.

**Also fixed en route.** Kuadrant AuthPolicy `patternMatching` supports `operator: matches` with a regex (used to allow exactly `energy-optimizer|judge-agent` to score); the scorer rate limit is 60/60s (EA-11 arithmetic: MCP spends ~3–4 gateway requests per tool call; scoring happens once per proposal round).

**Refs.** `302-energy-optimizer/agent/scorer-mcp/` (governed scorer tool + smoke), `agent/judge/` (judge + smoke), `agent/energy_optimizer.py` (arbiter), `deploy/ocp/rome/{scorer-mcp,scorer-gateway-policies,judge}.yaml`.

---

## EA-13 — Managed MLflow GenAI eval: Datasets/Judges APIs work; the trace-centric evaluate() harness does not (yet)

**Context.** 302 quant+qual: turning EA-12 into a measured, repeatable check with the managed MLflow's GenAI evaluation suite (client `mlflow==3.4.0` against the RHOAI 3.5 EA2 workspace-scoped server).

**What works (verified live on Rome, experiment `302-energy-optimizer`/id 8):**
- **Evaluation datasets** (`mlflow.genai.datasets.create_dataset` / `merge_records`) — the Datasets tab populates. Two sharp edges: `get_dataset(name=...)` did not resolve an existing dataset by name on this build, so every re-run created a duplicate (cleaned up via `search_datasets` + `delete_dataset`, keep-newest); and the EA-9 workspace shim MUST be written `def shim(*a, **kw)` — a positional-signature shim breaks `search_datasets`, which calls `http_request` with keyword arguments.
- **LLM-as-judge registration** (`mlflow.genai.judges.make_judge(...).register(experiment_id=...)`) — the Judges tab populates; the judge model URI `openai:/kimi-linear-48b-a3b` with `OPENAI_API_BASE` pointed at the cluster's own vLLM predictor works for invocation too (8/8 judge calls served by in-cluster Kimi, no external LLM).

**What doesn't:** `mlflow.genai.evaluate()` is **trace-centric**, and the managed server's workspace proxy does not expose the 3.4 client's trace-span ingest endpoint: span export fails with `ENDPOINT_NOT_FOUND`, the harness trace never persists (`get_trace` OK client-side only), and the harness crashes — with expectations attached, at `get_expectation_assessments → trace.info.trace_id (NoneType)`; without them, at result assembly (`.to_json` on None). The Traces/Sessions tabs likewise cannot be fed from client-side tracing on this build.

**Working pattern (in the repo):** `302-energy-optimizer/eval/genai_eval.py` — create/merge the dataset and register the judge natively; attempt native `evaluate()` behind a try (with `SKIP_NATIVE=1` to skip the known-dead path on re-runs); on the trace wall, run the SAME predict_fn + SAME scorers manually and log a full evaluation run (per-row `log_table` + aggregate metrics) as a normal MLflow run.

**Bonus finding — rate limits vs evaluation workloads (EA-11's sibling).** The eval suite is a legitimate burst consumer of the governed scorer route: each row costs ~8 gateway requests (the judge's own grounding fetch + the harness's verification fetch), and the first full run tripped the scorer's 60/60s cap — the judge's fetches got `429` and every verdict came back empty. The limiter was doing its job against exactly the burst shape evals create. Fix: scorer cap 60→120/60s, eval paced to 1 worker + 6s between rows. Size Kuadrant rate limits for *all* legitimate consumers of a route — agents AND their evaluation harnesses.

**The measured result (8-row suite, live judge over A2A, real governed scorer, run `genai-eval-judge-groundedness`):** `decision_correctness 0.75`, `groundedness_numeric 0.875`, `qos_safety 0.875`. The suite caught, automatically, both defects EA-12 predicted: the EA-12 replica row (judge again accepted 7.67% against a 50% target on an ungrounded claim → `grounded=False`) and an outage-territory acceptance (judge accepted a 2.6% QoS drop → `safe=False` — independent validation of the arbiter's hard QoS rail). The deterministic groundedness scorer is the reliable auditor; the Kimi-backed LLM auditor ran 8/8 but needs output-format calibration before its rate is meaningful.

---

## EA-14 — Bootstrapping a calibrated model with a *computed* proxy target (no live labels), honestly

**Context.** 301's quant+qual co-decision needs a calibrated *quantitative* signal for remediation risk, but Rome has no live 5G core, so there are no real remediation-outcome labels ("did restarting the SMF during this incident cause instability?"). The rule was: no invented labels, no facade.

**What worked.** Train the model (`netops-remediation-risk`, GradientBoosting) on the **real** 5gprod per-minute KPI series, with a target that is a **documented deterministic function of quantities that are actually measured** — `risk = sigmoid(Z0 + Wb·base_disruptiveness + Wl·util·base + Ws·severity + Wbs·base·severity)`, where `base_disruptiveness` is the intrinsic churn each real playbook causes (scale_amf < rebalance_upf < rollback < restart_smf), `util` is the NF's measured cpu/mem/buffer load, and `severity` is deviation from the NF's healthy baseline grounded by the labelled alert windows. This is a distilled operational heuristic learned into a **served, versioned, governed** model — the value is the full lifecycle (MLflow → registry → KServe → Kuadrant `/plan-score` → observability) and a drop-in slot for real outcome labels later; the computed target is the replaceable bootstrap. Result on the real data: **r² ≈ 0.97, MAE ≈ 0.02 over 8,646 rows**, and it discriminates correctly (restart on a saturated/degraded SMF ≈ 0.8 high; scale on an idle/healthy AMF ≈ 0.2 low).

**Calibration finding.** The first target was **multiplicative-then-clip** (`base·(1+Wl·util·base)·(1+Ws·sev)`, clip 0–1). It **saturated at 1.0** for the high-disruptiveness actions — restart and rollback returned 1.0 across almost all incident severities, so the model had no resolution to learn and the quant gate lost discrimination at the dangerous end. Switching to a **bounded logistic** combination (same real inputs) spread risk across the full range and restored resolution (restart severe 0.82 vs quiet 0.75; scale stays ~0.2; rebalance/rollback land in the medium band where the judge's nuance matters). Lesson: when distilling a heuristic into a learnable target, use a bounded (logistic) form, not multiply-and-clip — clipping destroys the gradient the model needs and collapses the top of your scale.

## EA-15 — The one hard rail is course-specific: 302 fails to *reject*, 301 fails to *human-approval*

**Context.** Both 302 and 301 use the "full co-decider judge + exactly one non-negotiable rail" arbiter (EA-12). But the *safe state* the rail forces is not the same, and copying 302's rail verbatim into 301 would be wrong.

302 has no human in the loop, so its safe state is **reject**: a simulated QoS drop above the hard floor is rejected no matter what. 301 already has a human approval gate (`approval_required`, enforced in Execution's code), and rejecting a high-risk remediation outright can *strand a real incident* that genuinely needs a disruptive fix (you sometimes must restart the SMF). So 301's rail forces **human approval**, not rejection: a step whose calibrated risk breaches `HARD_RISK_FLOOR` (0.85) sets `approval_required=true` regardless of the judge — the co-decision may never mark a very-high-risk plan as *autonomously* actuatable, but it also never silently discards it. Verified offline across the truth table: consensus accept → autonomous; judge override in both directions → honoured with an audited override; hard rail → approval forced even when the judge accepts. Lesson: the arbiter *structure* (full co-decider + one rail + audited overrides) ports across courses; the rail's *forced state* must match what "safe" means for that loop — reject where there is no human fallback, escalate-to-human where there is.

## Quick reference — Rome platform facts

- Kuadrant CRDs are all `kuadrant.io/v1` (AuthPolicy, RateLimitPolicy); the `Kuadrant` CR is `kuadrant.io/v1beta1` and exposes `spec.mtls` / `spec.components` / `spec.observability`.
- Gateway class `data-science-gateway-class`, controller `openshift.io/gateway-controller` (OSSM/Istio); istiod is `istiod-openshift-gateway.openshift-ingress`.
- Authorino ext_authz: gRPC `:50051`, HTTP `:5001`; OIDC `:8083`. Limitador: HTTP `:8080`, gRPC `:8081`.
- Kuadrant wasm-shim is fetched at gateway-Envoy startup from `kuadrant-operator-wasm.kuadrant-system.svc:8082/plugin.wasm` (remote fetch, sha256-pinned); the auth action's `failureMode: deny` means any auth-service transport failure surfaces as a bare `500`.
