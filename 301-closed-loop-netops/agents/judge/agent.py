"""301 GenAI Judge agent — the qualitative half of the Planning
quant+qual co-decision.

Planning decides with two complementary signals. The classic
remediation-risk regressor gives a calibrated but blind-and-mute number
(risk 0..1, band) per proposed action. This agent adds the qualitative
half: an LLM (cluster Kimi, served through OGX — the article's name for
the agent-framework layer, on Llama Stack) that reasons FROM those
numbers — it never invents a risk — and renders a structured plan-level
verdict with rationale and flagged risks, catching context a scalar
cannot see (a maintenance window, an ordering hazard, blast radius on a
degraded neighbour).

Grounding is enforced by construction: the judge fetches each step's
calibrated risk ITSELF, by calling the classic model through the SAME
Kuadrant-governed gateway 301 uses for actuation (the remediation-risk
scorer tool on /plan-score), authenticated as its own ServiceAccount. So
the qualitative verdict is always anchored to the real quantitative
signal, and the judge's access to the classic model is itself governed
(WHO / HOW-OFTEN).

The judge is a full co-decider — its decision stands — but the arbiter
(the Planning agent) keeps one non-negotiable rail: a step that breaches
a hard risk floor / the governed scale cap forces approval or rejection
no matter what either signal says. Every judgment is logged to MLflow as
the audit record.

A2A message in: a JSON object with at least
  {"steps": [{"action": str, "target_nf": str, "anomaly_score": float,
              "utilization": float?, "severity": float?}, ...],
   "context": <str, optional>}
A2A message out: {"verdict": {...}, "quant_scores": [...], ...}.

Runs as deploy/ocp/rome/judge.yaml.
"""
import json
import os
import re
import uuid
from pathlib import Path

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
SCORE_GW = os.environ.get(
    "SCORE_GATEWAY_URL",
    "http://netops-gateway-data-science-gateway-class."
    "agent-school.svc.cluster.local:8080/plan-score")
LLAMA_STACK_URL = os.environ.get(
    "LLAMA_STACK_URL",
    "http://llama-stack.agent-school.svc.cluster.local:8321")
MODEL = os.environ.get("LS_MODEL", "kimi-linear-48b-a3b")


# ---------------------------------------------------------------- mlflow
def _enable_mlflow():
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        return None
    ws = os.environ.get("MLFLOW_WORKSPACE")
    try:
        if ws:
            if not os.environ.get("MLFLOW_TRACKING_TOKEN"):
                sa = Path(SA_DIR + "/token")
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
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT",
                                             "301-closed-loop"))
        print("[mlflow] enabled ->", uri, flush=True)
        return mlflow
    except Exception as e:  # noqa: BLE001
        print("[mlflow] disabled:", type(e).__name__, e, flush=True)
        return None


mlflow = _enable_mlflow()


# ----------------------------------------------- governed classic scorer
def _sa_token():
    return Path(SA_DIR + "/token").read_text().strip()


async def _mcp_score(step):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    args = {"action": step["action"], "target_nf": step["target_nf"],
            "anomaly_score": float(step.get("anomaly_score", 0.0))}
    if step.get("utilization") is not None:
        args["utilization"] = float(step["utilization"])
    if step.get("severity") is not None:
        args["severity"] = float(step["severity"])
    async with streamablehttp_client(
            SCORE_GW, headers={"Authorization": "Bearer " + _sa_token()},
            timeout=120) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("score_remediation", args)
            return json.loads(res.content[0].text)


def _run_async(coro):
    import asyncio
    import threading
    box = {}

    def runner():
        try:
            box["v"] = asyncio.run(coro)
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=runner)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


# --------------------------------------------------------------- reasoning
from llama_stack_client import LlamaStackClient  # noqa: E402
from llama_stack_client.lib.agents.agent import Agent  # noqa: E402

LS = LlamaStackClient(base_url=LLAMA_STACK_URL)

JUDGE_INSTRUCTIONS = """You are the QUALITATIVE judge in a 5G-core
closed-loop remediation planner's quant+qual co-decision. A classic
regressor has ALREADY produced a calibrated risk (0..1, and a band
low/medium/high) for EACH proposed remediation step; you must reason
FROM those risks and never invent or alter one.

You are given the ordered remediation plan (each step: a governed
playbook action on a target network function, with the incident's
anomaly score) and each step's calibrated risk from the model, plus any
operational context. Return a STRICT JSON object and NOTHING else:

{"decision":"accept|revise|reject",
 "confidence":0.0-1.0,
 "rationale":"one or two sentences grounded in the risk numbers",
 "risks":["short risk phrases the scalar can't see"],
 "cited_risks":[{"action":str,"target_nf":str,"risk":num,"band":str}, ...]}

Guidance: accept only if the step risks AND the operational context
support acting autonomously. A plan whose highest step risk is 'low' is
a strong accept; a 'high' step should usually be `revise` (reorder,
substitute a gentler action) or escalate; use `reject` when the numbers
or context make the plan unsafe. Always flag context a pure regressor
misses: actuation ordering hazards (restart before rebalance drains
sessions), acting during a maintenance/peak window, blast radius on an
already-degraded neighbour NF. You are a full co-decider: your decision
stands — but justify any disagreement with the numbers. Echo the exact
risks you were given in cited_risks. Return ONLY the JSON."""


def _parse_verdict(text):
    t = (text or "").strip()
    t = re.sub(r"^```(json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            pass
    return {"decision": "revise", "confidence": 0.0,
            "rationale": "unparseable judge output; defaulting to revise",
            "risks": ["judge_output_unparseable"], "raw": t[:400]}


def judge_plan(payload):
    steps = payload.get("steps") or []
    if not steps:
        return {"error": "no steps to judge"}
    context = payload.get("context", "none provided")

    quant = []
    for st in steps:
        q = _run_async(_mcp_score(st))
        if "error" in q:
            return {"error": "scorer call failed: " + str(q["error"]),
                    "quant_scores": quant + [q]}
        quant.append(q)

    lines = []
    for st, q in zip(steps, quant):
        lines.append("  step: %s on %s (anomaly=%.2f) -> risk=%.3f (%s)" % (
            st.get("action"), q.get("target_nf", st.get("target_nf")),
            float(st.get("anomaly_score", 0.0)),
            q.get("risk"), q.get("risk_band")))
    prompt = (
        "REMEDIATION PLAN (ordered):\n%s\n"
        "CLASSIC REGRESSOR RISK PER STEP: as annotated above.\n"
        "OPERATIONAL CONTEXT: %s\n"
        "Return the JSON plan verdict." % ("\n".join(lines), context))

    agent = Agent(LS, model=MODEL, instructions=JUDGE_INSTRUCTIONS,
                  enable_session_persistence=False)
    sid = agent.create_session("plan-judge-" + uuid.uuid4().hex[:8])
    turn = agent.create_turn(
        messages=[{"role": "user", "content": prompt}],
        session_id=sid, stream=False)
    verdict = _parse_verdict(turn.output_message.content)
    verdict.setdefault("cited_risks", [
        {"action": st.get("action"),
         "target_nf": q.get("target_nf", st.get("target_nf")),
         "risk": q.get("risk"), "band": q.get("risk_band")}
        for st, q in zip(steps, quant)])

    max_risk = max((float(q.get("risk") or 0.0) for q in quant), default=0.0)
    rec = {"verdict": verdict, "quant_scores": quant,
           "max_step_risk": max_risk, "model": MODEL}

    if mlflow:
        try:
            with mlflow.start_run(run_name="plan-judge-"
                                  + uuid.uuid4().hex[:8]):
                mlflow.log_param("decision", verdict.get("decision"))
                mlflow.log_param("model", MODEL)
                mlflow.log_param("steps", len(steps))
                mlflow.log_metric("confidence",
                                  float(verdict.get("confidence") or 0.0))
                mlflow.log_metric("max_step_risk", max_risk)
                mlflow.log_dict(rec, "judgment.json")
        except Exception as e:  # noqa: BLE001
            print("[mlflow] log skipped:", type(e).__name__, e, flush=True)

    return rec


# ------------------------------------------------------------------- a2a
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.apps import A2AStarletteApplication  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentSkill  # noqa: E402
from a2a.utils import new_agent_text_message  # noqa: E402


def _extract_payload(text):
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            pass
    return None


class JudgeExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        payload = _extract_payload(context.get_user_input() or "")
        if not payload or not payload.get("steps"):
            await event_queue.enqueue_event(new_agent_text_message(
                json.dumps({"error": "send a JSON body with a non-empty "
                                     "steps list"})))
            return
        try:
            rec = judge_plan(payload)
        except Exception as e:  # noqa: BLE001
            rec = {"error": type(e).__name__ + ": " + str(e)[:200]}
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(rec)))

    async def cancel(self, context, event_queue):
        raise Exception("cancel unsupported")


CARD = AgentCard(
    name="plan-judge",
    description=("Closed-loop NetOps GenAI judge: the qualitative half of "
                 "the Planning quant+qual co-decision. Grounds on the "
                 "classic remediation-risk regressor (reached through the "
                 "Kuadrant-governed scorer tool) and renders a structured "
                 "accept/revise/reject plan verdict with rationale and "
                 "risks."),
    url=os.environ.get(
        "A2A_PUBLIC_URL",
        "http://plan-judge-agent.agent-school.svc.cluster.local:8080/"),
    version="0.1.0",
    default_input_modes=["text"], default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(
        id="judge-plan", name="judge-plan",
        description="Judge a remediation plan (JSON with steps[] of "
                    "action/target_nf/anomaly_score, optional context).",
        tags=["netops", "genai", "judge", "co-decision", "5g"])])

app = A2AStarletteApplication(
    agent_card=CARD,
    http_handler=DefaultRequestHandler(
        agent_executor=JudgeExecutor(),
        task_store=InMemoryTaskStore())).build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
