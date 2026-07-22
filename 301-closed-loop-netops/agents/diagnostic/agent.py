"""301 Diagnostic agent — the loop's first worker, live on Rome.

LangGraph harness, A2A surface, externalized state (12-Factor Agent
discipline): the graph senses the live anomaly verdicts 101's pipeline
pushes to the Feast online store, reasons over them with the cluster's
own Kimi-Linear endpoint, writes its findings to the external workflow
state store (Redis), and answers over A2A. The pod holds no state a
retry would miss — kill it mid-loop and a fresh session picks up from
the store.

A2A: agent card at /.well-known/agent.json (a2a-sdk 0.3.22, pinned —
the 1.x line reshuffles the server API), JSON-RPC message/send. The
message text is a free-form diagnosis request; an optional
`loop_id=<id>` token pins the workflow id, otherwise one is minted.

Observability: every graph node and LLM call lands in MLflow
(experiment `301-closed-loop`, workspace agent-school) via
mlflow.langchain autolog — same workspace-header shims as every other
Rome workload.

Runs as deploy/ocp/rome/diagnostic.yaml.
"""
import json
import os
import ssl
import urllib.request
import uuid
from pathlib import Path

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
                # span ingest lives at the server root, not under /mlflow
                if endpoint == "/v1/traces" and host_creds.host.rstrip("/").endswith("/mlflow"):
                    import copy
                    host_creds = copy.copy(host_creds)
                    host_creds.host = host_creds.host.rstrip("/")[: -len("/mlflow")]
                return orig(host_creds, endpoint, method, *a,
                            extra_headers=headers, **kw)

            rest_utils.http_request = shim
            from mlflow.store.tracking import rest_store
            rest_store.http_request = shim

            # artifact uploads (log_dict/log_model) bypass rest_utils ->
            # inject the workspace header at the requests level too
            # (EA2 finding, same as the 202 pipeline register step)
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
        mlflow.langchain.autolog()
        print("[mlflow] autolog ->", uri, flush=True)
        return mlflow
    except Exception as e:  # tracing must never break the loop
        print("[mlflow] disabled:", type(e).__name__, e, flush=True)
        return None


mlflow = _enable_mlflow()

# ----------------------------------------------------------------- feast
FEAST_URL = os.environ["FEAST_ONLINE_URL"].rstrip("/")
FEAST_CA = os.environ.get(
    "FEAST_CA", "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt")
# KPI columns per NF, from the published 5gcore-prod dataset (the same
# headers 101's pipeline materializes as 1h aggregates).
NF_KPIS = {
    "amf": ["cpu_utilization", "memory_utilization", "registration_rate",
            "registration_success_rate", "session_setup_rate"],
    "smf": ["cpu_utilization", "memory_utilization",
            "session_establishment_rate", "session_success_rate"],
    "upf": ["cpu_utilization", "memory_utilization", "packet_drop_rate",
            "latency_ms", "throughput_mbps"],
}


def feast_online(nf, refs):
    ctx = ssl.create_default_context(
        cafile=FEAST_CA if Path(FEAST_CA).exists() else None)
    body = json.dumps({"features": refs, "entities": {"nf": [nf]}}).encode()
    req = urllib.request.Request(
        FEAST_URL + "/get-online-features", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        payload = json.loads(resp.read())
    names = payload["metadata"]["feature_names"]
    values = [r["values"][0] for r in payload["results"]]
    return {n: v for n, v in zip(names, values) if n != "nf"}


# ----------------------------------------------------------------- state
import redis as redislib  # noqa: E402

STATE = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                decode_responses=True)

# ------------------------------------------------------------- langgraph
from typing import TypedDict  # noqa: E402

from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402

LLM = ChatOpenAI(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ.get("LLM_API_KEY", "none"),
    model=os.environ["LLM_MODEL"],
    temperature=0.1, timeout=180)


class LoopState(TypedDict, total=False):
    loop_id: str
    request: str
    telemetry: dict
    findings: dict


def sense(state: LoopState) -> LoopState:
    tel = {}
    for nf, kpis in NF_KPIS.items():
        refs = [nf + "_kpis:anomaly_score", nf + "_kpis:anomaly_flag"] + \
               [nf + "_kpis:" + k + "_1h_mean" for k in kpis]
        try:
            feats = feast_online(nf, refs)
        except Exception as e:
            feats = {"error": type(e).__name__ + ": " + str(e)[:120]}
        tel[nf] = feats
    return {"telemetry": tel}


PROMPT = """You are the Diagnostic agent in a closed-loop network
operations system for a 5G core (AMF/SMF/UPF). Below are the LIVE
anomaly verdicts (score in [-1,1]: lower = more anomalous; flag 1 =
anomalous) produced by the platform's IsolationForest pipeline, plus 1h
KPI means, all retrieved from the feature store this instant.

Telemetry:
{telemetry}

Return STRICT JSON only, no prose, with keys:
  incident (bool) - is any NF in an anomalous or degraded condition;
  affected_nfs (list of strings);
  severity ("none"|"low"|"medium"|"high");
  evidence (list of short strings citing the specific values);
  hypothesis (one sentence: most likely cause);
  recommended_focus (one sentence: what Planning should look at first).
"""


def analyze(state: LoopState) -> LoopState:
    msg = LLM.invoke(PROMPT.format(
        telemetry=json.dumps(state["telemetry"], indent=1)))
    text = msg.content.strip()
    if text.startswith("```"):
        text = text.strip("`\n")
        if text.startswith("json"):
            text = text[4:]
    try:
        findings = json.loads(text)
    except Exception:
        findings = {"incident": None, "raw": text[:2000],
                    "error": "non-json-llm-output"}
    return {"findings": findings}


def publish(state: LoopState) -> LoopState:
    lid = state["loop_id"]
    rec = {"loop_id": lid, "stage": "diagnostic",
           "request": state.get("request", ""),
           "telemetry": state["telemetry"], "findings": state["findings"]}
    STATE.set("loop:" + lid + ":diagnostic", json.dumps(rec))
    STATE.set("loop:" + lid + ":status", "diagnosed")
    STATE.lpush("loop:index", lid)
    return {}


graph = StateGraph(LoopState)
graph.add_node("sense", sense)
graph.add_node("analyze", analyze)
graph.add_node("publish", publish)
graph.set_entry_point("sense")
graph.add_edge("sense", "analyze")
graph.add_edge("analyze", "publish")
graph.add_edge("publish", END)
DIAGNOSTIC = graph.compile()

# ------------------------------------------------------------------- a2a
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.apps import A2AStarletteApplication  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentSkill  # noqa: E402
from a2a.utils import new_agent_text_message  # noqa: E402


class DiagnosticExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        text = context.get_user_input() or "diagnose current network state"
        loop_id = None
        for tok in text.split():
            if tok.startswith("loop_id="):
                loop_id = tok.split("=", 1)[1]
        loop_id = loop_id or uuid.uuid4().hex[:12]
        run_kwargs = {}
        if mlflow:
            run_kwargs = {"run_name": "diagnostic-" + loop_id}
            run_ctx = mlflow.start_run(**run_kwargs)
        try:
            result = DIAGNOSTIC.invoke({"loop_id": loop_id, "request": text})
            if mlflow:
                try:  # tracing must never break the loop
                    mlflow.log_param("loop_id", loop_id)
                    f = result.get("findings", {})
                    if isinstance(f.get("incident"), bool):
                        mlflow.log_param("incident", f["incident"])
                        mlflow.log_param("severity", f.get("severity"))
                    mlflow.log_dict(result.get("findings", {}),
                                    "findings.json")
                except Exception as e:
                    print("[mlflow] log skipped:", type(e).__name__, e,
                          flush=True)
        finally:
            if mlflow:
                run_ctx.__exit__(None, None, None)
        payload = {"loop_id": loop_id, "findings": result.get("findings", {}),
                   "state_key": "loop:" + loop_id + ":diagnostic"}
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(payload)))

    async def cancel(self, context, event_queue):
        raise Exception("cancel unsupported")


CARD = AgentCard(
    name="diagnostic",
    description=("Closed-loop NetOps Diagnostic worker: senses live Feast "
                 "anomaly verdicts for the 5G core NFs, produces incident "
                 "findings, externalizes state, answers over A2A."),
    url=os.environ.get("A2A_PUBLIC_URL",
                       "http://diagnostic-agent.agent-school.svc.cluster.local:8080/"),
    version="0.1.0",
    default_input_modes=["text"], default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(
        id="diagnose", name="diagnose",
        description="Detect and characterize 5G core incidents from live "
                    "feature-store verdicts.",
        tags=["netops", "diagnosis", "5g"])])

app = A2AStarletteApplication(
    agent_card=CARD,
    http_handler=DefaultRequestHandler(
        agent_executor=DiagnosticExecutor(),
        task_store=InMemoryTaskStore())).build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
