"""301 Validation agent — closes the loop.

Fourth and final worker. Like Execution, deliberately deterministic:
no LLM. Validation is evidence math — re-read the live series after
actuation, compare against the pre-action baseline Diagnostic
recorded, and decide with fixed thresholds. Intelligence lives in the
middle of the loop (Diagnostic, Planning); both safety-critical ends
(Execution, Validation) are code.

Verdicts:
  improved     — anomaly flags cleared or scores moved up materially;
                 loop closes as resolved.
  stable       — no material movement either way; loop closes as
                 monitor (the anomaly persists; the next loop
                 iteration owns it). On Rome this is the expected
                 outcome — the published telemetry is a fixed dataset,
                 so the online verdicts do not react to the stand-in
                 NFs being scaled. Documented honestly in the README.
  deteriorated — the plan's rollback trigger territory: any NF's
                 anomaly score dropped by more than the threshold or a
                 clean NF turned anomalous. Validation then requests a
                 ROLLBACK — over A2A, through the Execution agent,
                 because the loop has exactly one actuation path and
                 Validation is not it.

Runs as deploy/ocp/rome/validation.yaml.
"""
import json
import os
import ssl
import urllib.request
from pathlib import Path
from uuid import uuid4

DETERIORATION_THRESHOLD = 0.05  # anomaly-score drop that trips rollback


# ---------------------------------------------------------------- mlflow
def _enable_mlflow():
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        return None
    ws = os.environ.get("MLFLOW_WORKSPACE")
    try:
        if ws:
            if not os.environ.get("MLFLOW_TRACKING_TOKEN"):
                sa = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
                if sa.exists():
                    os.environ["MLFLOW_TRACKING_TOKEN"] = sa.read_text().strip()
            os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
            from mlflow.utils import rest_utils
            orig = rest_utils.http_request

            def shim(host_creds, endpoint, method, *a, **kw):
                headers = dict(kw.pop("extra_headers", None) or {})
                headers["X-MLFLOW-WORKSPACE"] = ws
                return orig(host_creds, endpoint, method, *a,
                            extra_headers=headers, **kw)

            rest_utils.http_request = shim
            from mlflow.store.tracking import rest_store
            rest_store.http_request = shim

            import requests as rq
            orig_req = rq.Session.request

            def req(self, method, url, **kw):
                if "mlflow" in url:
                    h = kw.get("headers") or {}
                    h["X-MLFLOW-WORKSPACE"] = ws
                    kw["headers"] = h
                return orig_req(self, method, url, **kw)

            rq.Session.request = req
        import mlflow
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT", "301-closed-loop"))
        print("[mlflow] enabled ->", uri, flush=True)
        return mlflow
    except Exception as e:
        print("[mlflow] disabled:", type(e).__name__, e, flush=True)
        return None


mlflow = _enable_mlflow()

# ----------------------------------------------------------------- state
import redis as redislib  # noqa: E402

STATE = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                decode_responses=True)

EXECUTION_URL = os.environ.get(
    "EXECUTION_URL",
    "http://execution-agent.agent-school.svc.cluster.local:8080")

# ----------------------------------------------------------------- feast
FEAST_URL = os.environ["FEAST_ONLINE_URL"].rstrip("/")
FEAST_CA = os.environ.get(
    "FEAST_CA", "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt")
NFS = ["amf", "smf", "upf"]


def sense_post():
    """Fresh anomaly verdicts, straight from the online store."""
    ctx = ssl.create_default_context(
        cafile=FEAST_CA if Path(FEAST_CA).exists() else None)
    out = {}
    for nf in NFS:
        body = json.dumps({
            "features": [nf + "_kpis:anomaly_score",
                         nf + "_kpis:anomaly_flag"],
            "entities": {"nf": [nf]}}).encode()
        req = urllib.request.Request(
            FEAST_URL + "/get-online-features", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
                payload = json.loads(r.read())
            names = payload["metadata"]["feature_names"]
            values = [x["values"][0] for x in payload["results"]]
            feats = {n: v for n, v in zip(names, values)}
            out[nf] = {"anomaly_score": feats.get("anomaly_score"),
                       "anomaly_flag": feats.get("anomaly_flag")}
        except Exception as e:
            out[nf] = {"error": type(e).__name__ + ": " + str(e)[:100]}
    return out


def assess(pre, post):
    """Deterministic verdict from pre/post anomaly evidence."""
    deltas, worsened, improved_nfs = {}, [], []
    for nf in NFS:
        p0 = (pre.get(nf) or {}).get("anomaly_score")
        p1 = (post.get(nf) or {}).get("anomaly_score")
        f0 = (pre.get(nf) or {}).get("anomaly_flag")
        f1 = (post.get(nf) or {}).get("anomaly_flag")
        if p0 is None or p1 is None:
            deltas[nf] = None
            continue
        d = round(float(p1) - float(p0), 4)
        deltas[nf] = d
        if d < -DETERIORATION_THRESHOLD or (
                f0 in (0, 0.0) and f1 in (1, 1.0)):
            worsened.append(nf)
        if f1 in (0, 0.0) and f0 in (1, 1.0):
            improved_nfs.append(nf)
        elif d > DETERIORATION_THRESHOLD:
            improved_nfs.append(nf)
    if worsened:
        return "deteriorated", deltas, worsened
    if improved_nfs and len(improved_nfs) == len(
            [nf for nf in NFS if deltas.get(nf) is not None]):
        return "improved", deltas, improved_nfs
    return "stable", deltas, []


async def request_rollback(loop_id):
    """The single actuation path: ask Execution, over A2A."""
    import httpx
    from a2a.client import A2ACardResolver, A2AClient
    from a2a.types import MessageSendParams, SendMessageRequest

    async with httpx.AsyncClient(timeout=600) as hc:
        card = await A2ACardResolver(
            httpx_client=hc, base_url=EXECUTION_URL).get_agent_card()
        client = A2AClient(httpx_client=hc, agent_card=card)
        req = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(
            message={"role": "user", "message_id": uuid4().hex,
                     "parts": [{"kind": "text",
                                "text": "rollback loop_id=" + loop_id}]}))
        resp = await client.send_message(req)
        return json.loads(resp.root.result.parts[0].root.text)


async def validate(loop_id):
    status = STATE.get("loop:" + loop_id + ":status")
    if status != "executed":
        return {"error": "loop status is '" + str(status) +
                         "', expected executed"}
    diag = json.loads(STATE.get("loop:" + loop_id + ":diagnostic") or "{}")
    plan = json.loads(STATE.get("loop:" + loop_id + ":plan") or "{}")
    pre = diag.get("telemetry", {})
    post = sense_post()
    verdict, deltas, affected = assess(pre, post)

    rec = {"loop_id": loop_id, "stage": "validation",
           "verdict": verdict, "score_deltas": deltas,
           "affected_nfs": affected,
           "rollback_trigger": (plan.get("plan", {})
                                .get("rollback", {})),
           "post_verdicts": post}
    if verdict == "deteriorated":
        rb = await request_rollback(loop_id)
        rec["rollback"] = rb
        # execution sets status=rolled_back; keep validation record too
        STATE.set("loop:" + loop_id + ":validation", json.dumps(rec))
        return rec
    final = "resolved" if verdict == "improved" else "monitor"
    rec["disposition"] = final
    STATE.set("loop:" + loop_id + ":validation", json.dumps(rec))
    STATE.set("loop:" + loop_id + ":status", "validated_" + final)
    return rec


# ------------------------------------------------------------------- a2a
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.apps import A2AStarletteApplication  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentSkill  # noqa: E402
from a2a.utils import new_agent_text_message  # noqa: E402


class ValidationExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        text = context.get_user_input() or ""
        loop_id = None
        for tok in text.split():
            if tok.startswith("loop_id="):
                loop_id = tok.split("=", 1)[1]
        if not loop_id:
            await event_queue.enqueue_event(new_agent_text_message(
                json.dumps({"error": "message must carry loop_id=<id>"})))
            return
        run_ctx = mlflow.start_run(run_name="validation-" + loop_id) \
            if mlflow else None
        try:
            rec = await validate(loop_id)
            if mlflow:
                try:
                    mlflow.log_param("loop_id", loop_id)
                    if rec.get("verdict"):
                        mlflow.log_param("verdict", rec["verdict"])
                        mlflow.log_param("disposition",
                                         rec.get("disposition",
                                                 "rolled_back"))
                        for nf, d in (rec.get("score_deltas") or {}).items():
                            if d is not None:
                                mlflow.log_metric("delta_" + nf, d)
                    mlflow.log_dict(rec, "validation.json")
                except Exception as e:
                    print("[mlflow] log skipped:", type(e).__name__, e,
                          flush=True)
        finally:
            if run_ctx:
                run_ctx.__exit__(None, None, None)
        rec["state_key"] = "loop:" + loop_id + ":validation"
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(rec)))

    async def cancel(self, context, event_queue):
        raise Exception("cancel unsupported")


CARD = AgentCard(
    name="validation",
    description=("Closed-loop NetOps Validation worker: re-reads the live "
                 "anomaly verdicts after Execution, compares against the "
                 "pre-action baseline with fixed thresholds, closes the "
                 "loop or requests rollback through Execution. No LLM."),
    url=os.environ.get("A2A_PUBLIC_URL",
                       "http://validation-agent.agent-school.svc.cluster.local:8080/"),
    version="0.1.0",
    default_input_modes=["text"], default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(
        id="validate", name="validate",
        description="Validate an executed loop iteration (loop_id=<id>); "
                    "verdict improved|stable|deteriorated, rollback on "
                    "deterioration.",
        tags=["netops", "validation", "rollback", "governance"])])

app = A2AStarletteApplication(
    agent_card=CARD,
    http_handler=DefaultRequestHandler(
        agent_executor=ValidationExecutor(),
        task_store=InMemoryTaskStore())).build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
