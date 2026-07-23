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

quant+qual co-decision: the drafted plan is only a *candidate*. Before
publish, a `codecide` node grounds its risk and approval on two signals
that meet in a code arbiter — the calibrated remediation-risk regressor
(quant, reached only through the Kuadrant-governed /plan-score tool) and
the GenAI plan-judge (qual, A2A, grounded on the same governed scorer).
The judge is a full co-decider; one non-negotiable rail (a step risk
above HARD_RISK_FLOOR) forces human approval regardless. The grounded
risk/approval OVERWRITE the LLM's self-assessment, and every
judge-vs-quant disagreement is recorded as an audited override —
mirroring 302's arbiter, now on the actuation-planning side. Judge
unreachable → the quant gate alone, honestly recorded.

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

# ------------------------------------------------- quant+qual co-decision
# The plan the LLM drafts from the think-tank determination is a
# *candidate*. Before it is published, two grounded signals co-decide its
# risk and whether it may actuate autonomously:
#   quant — the calibrated remediation-risk regressor, reached ONLY
#           through the Kuadrant-governed scorer tool (/plan-score).
#   qual  — the GenAI plan-judge (A2A), which grounds itself on the SAME
#           governed scorer and returns accept/revise/reject.
# The judge is a full co-decider (its decision stands) except one
# non-negotiable rail: a step whose calibrated risk breaches
# HARD_RISK_FLOOR forces human approval no matter what either signal says.
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
SCORE_GW = os.environ.get(
    "SCORE_GATEWAY_URL",
    "http://netops-gateway-data-science-gateway-class."
    "agent-school.svc.cluster.local:8080/plan-score")
JUDGE_URL = os.environ.get(
    "JUDGE_URL",
    "http://plan-judge-agent.agent-school.svc.cluster.local:8080")
# quant gate: a max step risk at/above this band (high) is not
# auto-approvable by the quant signal alone.
QUANT_RISK_CEILING = float(os.environ.get("QUANT_RISK_CEILING", "0.66"))
# non-negotiable rail: a step risk at/above this forces human approval.
HARD_RISK_FLOOR = float(os.environ.get("HARD_RISK_FLOOR", "0.85"))
ACTION_CATALOG = {"scale_amf", "rebalance_upf", "restart_smf", "rollback"}


def _sa_token():
    return Path(SA_DIR + "/token").read_text().strip()


def _anom01(nf_tel):
    """101's Feast anomaly_score is in [-1,1] (lower = more anomalous);
    map to a 0..1 severity where higher = worse."""
    try:
        s = float(nf_tel.get("anomaly_score"))
    except (TypeError, ValueError):
        return 0.5
    return min(max(0.5 - 0.5 * s, 0.0), 1.0)


async def _mcp_score(step):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    args = {"action": step["action"], "target_nf": step["target_nf"],
            "anomaly_score": float(step.get("anomaly_score", 0.0))}
    async with streamablehttp_client(
            SCORE_GW, headers={"Authorization": "Bearer " + _sa_token()},
            timeout=120) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("score_remediation", args)
            return json.loads(res.content[0].text)


async def _ask_judge(steps, context):
    import httpx
    from a2a.client import A2ACardResolver, A2AClient
    from a2a.types import MessageSendParams, SendMessageRequest
    body = json.dumps({"steps": steps, "context": context})
    async with httpx.AsyncClient(timeout=300) as hc:
        card = await A2ACardResolver(httpx_client=hc,
                                     base_url=JUDGE_URL).get_agent_card()
        client = A2AClient(httpx_client=hc, agent_card=card)
        req = SendMessageRequest(id=uuid.uuid4().hex, params=MessageSendParams(
            message={"role": "user", "message_id": uuid.uuid4().hex,
                     "parts": [{"kind": "text", "text": body}]}))
        resp = await client.send_message(req)
        return json.loads(resp.root.result.parts[0].root.text)


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
    telemetry: dict
    determination: dict
    plan: dict
    codecision: dict
    error: str


def fetch(state: LoopState) -> LoopState:
    raw = STATE.get("loop:" + state["loop_id"] + ":diagnostic")
    if not raw:
        return {"error": "no diagnostic record for loop " + state["loop_id"]}
    rec = json.loads(raw)
    # telemetry carries the per-NF anomaly_score the co-decision grounds on
    return {"findings": rec.get("findings", {}),
            "telemetry": rec.get("telemetry", {})}


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


async def codecide(state: LoopState) -> LoopState:
    """Quant + qual co-decision over the candidate plan. Grounds the
    plan's risk/approval on a calibrated model (through the governed
    scorer tool) and a GenAI judge, and records every disagreement."""
    if state.get("error"):
        return {}
    plan = dict(state.get("plan") or {})
    steps = plan.get("steps") or []
    tel = state.get("telemetry") or {}

    # Build scoring steps from the plan's governed playbook actions.
    sc_steps = []
    for st in steps:
        action = str(st.get("playbook", "")).replace(".yml", "").replace(
            ".yaml", "")
        nf = st.get("target_nf")
        if action in ACTION_CATALOG and nf:
            sc_steps.append({"action": action, "target_nf": nf,
                             "anomaly_score": _anom01(tel.get(nf, {}))})

    if not sc_steps:
        # nothing actuable to score (e.g. empty flow) — leave as-is,
        # record that the co-decision had no steps.
        return {"codecision": {"scored_steps": 0, "note": "no governed "
                               "actions to score", "final": "noop"}}

    # ---- Signal 1 (quant): calibrated risk via the governed scorer -----
    quant = []
    for s in sc_steps:
        try:
            quant.append(await _mcp_score(s))
        except Exception as e:  # noqa: BLE001
            quant.append({"error": type(e).__name__ + ": " + str(e)[:150]})
    risks = [float(q["risk"]) for q in quant
             if isinstance(q.get("risk"), (int, float))]
    max_risk = max(risks) if risks else 0.0
    scorer_ok = all("error" not in q for q in quant) and bool(risks)
    quant_ok = scorer_ok and max_risk < QUANT_RISK_CEILING

    # ---- Signal 2 (qual): the GenAI plan-judge, grounded on the same ---
    context = ("closed-loop remediation for a 5G core; affected NFs %s; "
               "playbook-catalog actions only; execution is ordered and "
               "governed (scale cap enforced at the actuator)."
               % state.get("findings", {}).get("affected_nfs", []))
    try:
        judgment = await _ask_judge(sc_steps, context)
        j = judgment.get("verdict", {}) if isinstance(judgment, dict) else {}
        if not j:
            raise ValueError("empty judge verdict")
    except Exception as e:  # noqa: BLE001
        # Judge unreachable -> quant gate alone, honestly recorded. The
        # loop must not die with the judge.
        judgment = {"error": type(e).__name__ + ": " + str(e)[:150]}
        j = {"decision": "accept" if quant_ok else "revise",
             "confidence": 0.0,
             "rationale": "judge unavailable; quant gate applied",
             "risks": ["judge_unavailable"]}
    decision = j.get("decision")

    # ---- Arbiter: full co-decider + one non-negotiable rail ------------
    hard_rail = max_risk >= HARD_RISK_FLOOR
    autonomous = (decision == "accept") and not hard_rail
    approval_required = not autonomous
    override = ((decision == "accept") != quant_ok) and not hard_rail
    band = ("high" if max_risk >= QUANT_RISK_CEILING
            else "medium" if max_risk >= 0.33 else "low")

    arbiter = {
        "max_step_risk": round(max_risk, 3),
        "quant_ok": quant_ok, "scorer_reachable": scorer_ok,
        "judge_decision": decision,
        "judge_confidence": j.get("confidence"),
        "hard_risk_rail_tripped": hard_rail,
        "override": override,
        "override_note": (
            "judge %s what the quant gate would have %s: %s"
            % ("cleared for autonomous run" if autonomous else "held for "
               "approval",
               "held" if quant_ok is False else "cleared",
               str(j.get("rationale"))[:200])) if override else None,
        "autonomous": autonomous,
        "final": decision,
        "thresholds": {"quant_risk_ceiling": QUANT_RISK_CEILING,
                       "hard_risk_floor": HARD_RISK_FLOOR}}

    # The grounded co-decision OVERWRITES the LLM's self-assessed
    # risk/approval — Execution enforces approval_required in code.
    plan["risk"] = band
    plan["approval_required"] = approval_required
    plan["codecision_disposition"] = decision
    codecision = {"scored_steps": len(sc_steps), "steps": sc_steps,
                  "quant_scores": quant, "judgment": judgment,
                  "arbiter": arbiter}
    print("[codecide]", json.dumps(arbiter)[:300], flush=True)
    return {"plan": plan, "codecision": codecision}


def publish(state: LoopState) -> LoopState:
    if state.get("error"):
        return {}
    lid = state["loop_id"]
    rec = {"loop_id": lid, "stage": "planning",
           "determination": state.get("determination", {}),
           "plan": state.get("plan", {}),
           "codecision": state.get("codecision", {})}
    STATE.set("loop:" + lid + ":plan", json.dumps(rec))
    STATE.set("loop:" + lid + ":status", "planned")
    return {}


graph = StateGraph(LoopState)
graph.add_node("fetch", fetch)
graph.add_node("consult", consult)
graph.add_node("plan", plan)
graph.add_node("codecide", codecide)
graph.add_node("publish", publish)
graph.set_entry_point("fetch")
graph.add_edge("fetch", "consult")
graph.add_edge("consult", "plan")
graph.add_edge("plan", "codecide")
graph.add_edge("codecide", "publish")
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
                    cod = result.get("codecision", {})
                    arb = cod.get("arbiter", {}) if cod else {}
                    if arb:
                        mlflow.log_param("judge_decision",
                                         arb.get("judge_decision"))
                        mlflow.log_param("quant_ok", arb.get("quant_ok"))
                        mlflow.log_param("override", arb.get("override"))
                        mlflow.log_param("hard_risk_rail",
                                         arb.get("hard_risk_rail_tripped"))
                        mlflow.log_param("autonomous", arb.get("autonomous"))
                        if arb.get("max_step_risk") is not None:
                            mlflow.log_metric("max_step_risk",
                                              float(arb["max_step_risk"]))
                    mlflow.log_dict(
                        {"determination": result.get("determination", {}),
                         "plan": p, "codecision": cod}, "plan.json")
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
