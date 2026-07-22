"""301 Planning agent — turns Diagnostic findings into a governed plan.

Second worker of the loop. Same discipline as Diagnostic (LangGraph
harness, A2A surface, externalized state, MLflow observability), plus
the article's external-reasoning boundary: before writing a plan, the
agent consults the MCP think-tank (separate namespace, reached only
over streamable-HTTP MCP) for the remediation-flow determination, then
merges that determination with the findings into a concrete plan over
the governed playbook catalog. The think-tank's raw determination is
preserved in the plan record — the external black box stays auditable
from the loop's side.

Input over A2A: a message containing `loop_id=<id>` for a loop that
Diagnostic has already written (`loop:<id>:diagnostic` in the state
store). Output: the plan JSON; state moves to `status=planned`.

Runs as deploy/ocp/rome/planning.yaml.
"""
import json
import os
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
                if endpoint == "/v1/traces" and host_creds.host.rstrip("/").endswith("/mlflow"):
                    import copy
                    host_creds = copy.copy(host_creds)
                    host_creds.host = host_creds.host.rstrip("/")[: -len("/mlflow")]
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
        mlflow.langchain.autolog()
        print("[mlflow] autolog ->", uri, flush=True)
        return mlflow
    except Exception as e:
        print("[mlflow] disabled:", type(e).__name__, e, flush=True)
        return None


mlflow = _enable_mlflow()

# ----------------------------------------------------------------- state
import redis as redislib  # noqa: E402

STATE = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                decode_responses=True)

THINKTANK_URL = os.environ["THINKTANK_MCP_URL"]

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
    findings: dict
    determination: dict
    plan: dict
    error: str


def fetch(state: LoopState) -> LoopState:
    raw = STATE.get("loop:" + state["loop_id"] + ":diagnostic")
    if not raw:
        return {"error": "no diagnostic record for loop " + state["loop_id"]}
    return {"findings": json.loads(raw).get("findings", {})}


async def consult(state: LoopState) -> LoopState:
    """External reasoning across the trust boundary — MCP over
    streamable-HTTP to the think-tank namespace."""
    if state.get("error"):
        return {}
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(THINKTANK_URL, timeout=300) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(
                "determine_remediation_flow",
                {"findings": json.dumps(state["findings"])})
    try:
        return {"determination": json.loads(res.content[0].text)}
    except Exception:
        return {"determination": {"error": "bad think-tank payload"}}


PROMPT = """You are the Planning agent in a closed-loop network
operations system for a 5G core. Combine the Diagnostic findings and
the external think-tank's remediation-flow determination into one
concrete, governed remediation plan. Do not invent actions outside the
determination's playbooks. If the determination's flow is empty,
return an empty steps list.

Diagnostic findings:
{findings}

Think-tank determination:
{determination}

Return STRICT JSON only, no prose, with keys:
  steps (ordered list: {{"order": n, "playbook": str, "target_nf": str,
         "expected_impact": short string}});
  risk ("low"|"medium"|"high");
  approval_required (bool - true if risk is not low or severity was
                     high);
  rollback (object: {{"trigger": str, "action": str}});
  summary (one sentence for the human operator).
"""


def plan(state: LoopState) -> LoopState:
    if state.get("error"):
        return {}
    msg = LLM.invoke(PROMPT.format(
        findings=json.dumps(state["findings"], indent=1),
        determination=json.dumps(state["determination"], indent=1)))
    text = msg.content.strip()
    if text.startswith("```"):
        text = text.strip("`\n")
        if text.startswith("json"):
            text = text[4:]
    try:
        p = json.loads(text)
    except Exception:
        p = {"error": "non-json-llm-output", "raw": text[:2000]}
    return {"plan": p}


def publish(state: LoopState) -> LoopState:
    if state.get("error"):
        return {}
    lid = state["loop_id"]
    rec = {"loop_id": lid, "stage": "planning",
           "determination": state.get("determination", {}),
           "plan": state.get("plan", {})}
    STATE.set("loop:" + lid + ":plan", json.dumps(rec))
    STATE.set("loop:" + lid + ":status", "planned")
    return {}


graph = StateGraph(LoopState)
graph.add_node("fetch", fetch)
graph.add_node("consult", consult)
graph.add_node("plan", plan)
graph.add_node("publish", publish)
graph.set_entry_point("fetch")
graph.add_edge("fetch", "consult")
graph.add_edge("consult", "plan")
graph.add_edge("plan", "publish")
graph.add_edge("publish", END)
PLANNING = graph.compile()

# ------------------------------------------------------------------- a2a
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.apps import A2AStarletteApplication  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentSkill  # noqa: E402
from a2a.utils import new_agent_text_message  # noqa: E402


class PlanningExecutor(AgentExecutor):
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
        run_ctx = mlflow.start_run(run_name="planning-" + loop_id) \
            if mlflow else None
        try:
            result = await PLANNING.ainvoke({"loop_id": loop_id})
            if mlflow:
                try:
                    mlflow.log_param("loop_id", loop_id)
                    p = result.get("plan", {})
                    if isinstance(p.get("risk"), str):
                        mlflow.log_param("risk", p["risk"])
                        mlflow.log_param("approval_required",
                                         p.get("approval_required"))
                        mlflow.log_param("steps", len(p.get("steps", [])))
                    mlflow.log_dict(
                        {"determination": result.get("determination", {}),
                         "plan": p}, "plan.json")
                except Exception as e:
                    print("[mlflow] log skipped:", type(e).__name__, e,
                          flush=True)
        finally:
            if run_ctx:
                run_ctx.__exit__(None, None, None)
        payload = {"loop_id": loop_id,
                   "error": result.get("error"),
                   "plan": result.get("plan", {}),
                   "state_key": "loop:" + loop_id + ":plan"}
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(payload)))

    async def cancel(self, context, event_queue):
        raise Exception("cancel unsupported")


CARD = AgentCard(
    name="planning",
    description=("Closed-loop NetOps Planning worker: reads Diagnostic "
                 "findings from the state store, consults the external MCP "
                 "think-tank for the remediation-flow determination, and "
                 "publishes a governed remediation plan."),
    url=os.environ.get("A2A_PUBLIC_URL",
                       "http://planning-agent.agent-school.svc.cluster.local:8080/"),
    version="0.1.0",
    default_input_modes=["text"], default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(
        id="plan", name="plan",
        description="Produce a governed remediation plan for a diagnosed "
                    "loop iteration (requires loop_id=<id>).",
        tags=["netops", "planning", "mcp", "5g"])])

app = A2AStarletteApplication(
    agent_card=CARD,
    http_handler=DefaultRequestHandler(
        agent_executor=PlanningExecutor(),
        task_store=InMemoryTaskStore())).build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
