"""302 GenAI Judge agent — the qualitative half of the co-decision.

The energy optimizer decides with two complementary signals. The
classic sustainability regressor gives a calibrated but blind-and-mute
number (energy_efficiency, predicted_fault_rate). This agent adds the
qualitative half: an LLM (cluster Kimi, served through OGX — the
article's name for the agent-framework layer, implemented on Llama
Stack) that reasons FROM that number — it never invents a score — and
renders a structured verdict with rationale and flagged risks, catching
context the scalar cannot see (coverage during events, policy zones,
over-aggressive sleep).

Grounding is enforced by construction: the judge fetches the calibrated
number itself, by calling the classic model through the SAME
Kuadrant-governed gateway 301 uses for actuation (the scorer MCP tool on
/score), authenticated as its own ServiceAccount. So the qualitative
verdict is always anchored to the real quantitative signal, and the
judge's access to the classic model is itself governed (WHO/HOW-OFTEN).

The judge is a full co-decider — its decision stands — but the arbiter
(the optimizer, Step 3) keeps one non-negotiable rail: a proposal that
breaches a hard QoS floor is rejected no matter what either signal says.
Every judgment is logged to MLflow as the audit record.

A2A message in: a JSON object with at least
  {"savings_pct": float, "qos_dropped_pct": float,
   "proposal": <str/obj, optional>, "context": <str, optional>}
A2A message out: {"verdict": {...}, "quant_score": {...}, ...}.

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
    "agent-school.svc.cluster.local:8080/score")
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
                                             "302-energy-optimizer"))
        print("[mlflow] enabled ->", uri, flush=True)
        return mlflow
    except Exception as e:  # noqa: BLE001
        print("[mlflow] disabled:", type(e).__name__, e, flush=True)
        return None


mlflow = _enable_mlflow()


# ----------------------------------------------- governed classic scorer
def _sa_token():
    return Path(SA_DIR + "/token").read_text().strip()


async def _mcp_score(savings_pct, qos_dropped_pct):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(
            SCORE_GW, headers={"Authorization": "Bearer " + _sa_token()},
            timeout=120) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("score_condition",
                                    {"savings_pct": savings_pct,
                                     "qos_dropped_pct": qos_dropped_pct})
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

JUDGE_INSTRUCTIONS = """You are the QUALITATIVE judge in a RAN energy
optimizer's quant+qual co-decision. A classic regressor has ALREADY
produced the calibrated numbers; you must reason FROM them and never
invent or alter a score.

You are given a cell-sleep proposal, the simulation's savings and QoS
impact, and the regressor's energy_efficiency (higher is better) and
predicted_fault_rate (lower is better). Return a STRICT JSON object and
NOTHING else:

{"decision":"accept|revise|reject",
 "confidence":0.0-1.0,
 "rationale":"one or two sentences grounded in the numbers",
 "risks":["short risk phrases the scalar can't see"],
 "cited_score":{"energy_efficiency":<num>,"predicted_fault_rate":<num>}}

Guidance: accept only if BOTH the numbers and the operational context
support it. Reasonable bar: savings materially positive, QoS impact
small, energy_efficiency healthy (~>=60), predicted_fault_rate not
elevated. Use `revise` when the idea is sound but too aggressive; use
`reject` when the numbers or context make it unsafe. Always flag context
risks a pure regressor misses (coverage during peak/events, protected
zones, sleeping too many cells). You are a full co-decider: your decision
stands — but justify any disagreement with the numbers. Echo the exact
scores you were given in cited_score. Return ONLY the JSON."""


def _parse_verdict(text):
    t = text.strip()
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


def judge_proposal(payload):
    savings = float(payload["savings_pct"])
    drop = float(payload["qos_dropped_pct"])
    proposal = payload.get("proposal", "(sleep-window proposal, unspecified)")
    context = payload.get("context", "none provided")

    quant = _run_async(_mcp_score(savings, drop))
    if "error" in quant:
        return {"error": "scorer call failed: " + str(quant["error"]),
                "quant_score": quant}

    prompt = (
        "PROPOSAL: %s\n"
        "SIMULATION: savings_pct=%.3f, qos_dropped_pct=%.3f\n"
        "CLASSIC REGRESSOR SCORE: energy_efficiency=%s, "
        "predicted_fault_rate=%s\n"
        "OPERATIONAL CONTEXT: %s\n"
        "Return the JSON verdict." % (
            json.dumps(proposal)[:600], savings, drop,
            quant.get("energy_efficiency"), quant.get("predicted_fault_rate"),
            context))

    agent = Agent(LS, model=MODEL, instructions=JUDGE_INSTRUCTIONS,
                  enable_session_persistence=False)
    sid = agent.create_session("judge-" + uuid.uuid4().hex[:8])
    turn = agent.create_turn(
        messages=[{"role": "user", "content": prompt}],
        session_id=sid, stream=False)
    verdict = _parse_verdict(turn.output_message.content)
    verdict.setdefault("cited_score", {
        "energy_efficiency": quant.get("energy_efficiency"),
        "predicted_fault_rate": quant.get("predicted_fault_rate")})

    rec = {"verdict": verdict, "quant_score": quant,
           "savings_pct": savings, "qos_dropped_pct": drop,
           "model": MODEL}

    if mlflow:
        try:
            with mlflow.start_run(run_name="judge-" + uuid.uuid4().hex[:8]):
                mlflow.log_param("decision", verdict.get("decision"))
                mlflow.log_param("model", MODEL)
                mlflow.log_metric("confidence",
                                  float(verdict.get("confidence") or 0.0))
                mlflow.log_metric("savings_pct", savings)
                mlflow.log_metric("qos_dropped_pct", drop)
                mlflow.log_metric("energy_efficiency",
                                  float(quant.get("energy_efficiency") or 0))
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
        if not payload or "savings_pct" not in payload \
                or "qos_dropped_pct" not in payload:
            await event_queue.enqueue_event(new_agent_text_message(
                json.dumps({"error": "send a JSON body with savings_pct "
                                     "and qos_dropped_pct"})))
            return
        try:
            rec = judge_proposal(payload)
        except Exception as e:  # noqa: BLE001
            rec = {"error": type(e).__name__ + ": " + str(e)[:200]}
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(rec)))

    async def cancel(self, context, event_queue):
        raise Exception("cancel unsupported")


CARD = AgentCard(
    name="judge",
    description=("Energy-optimizer GenAI judge: the qualitative half of a "
                 "quant+qual co-decision. Grounds on the classic "
                 "sustainability regressor (reached through the "
                 "Kuadrant-governed scorer tool) and renders a structured "
                 "accept/revise/reject verdict with rationale and risks."),
    url=os.environ.get("A2A_PUBLIC_URL",
                       "http://judge-agent.agent-school.svc.cluster.local:8080/"),
    version="0.1.0",
    default_input_modes=["text"], default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(
        id="judge", name="judge",
        description="Judge a cell-sleep proposal (JSON with savings_pct, "
                    "qos_dropped_pct, optional proposal/context).",
        tags=["energy", "genai", "judge", "co-decision"])])

app = A2AStarletteApplication(
    agent_card=CARD,
    http_handler=DefaultRequestHandler(
        agent_executor=JudgeExecutor(),
        task_store=InMemoryTaskStore())).build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
