"""301 Execution agent — the governed actuation arm of the loop.

Third worker. Deliberately the least clever component in the system:
no LLM anywhere in this pod. Execution reads the governed plan the
Planning agent published, enforces the approval gate, and actuates the
plan's steps — but as of Track 4 it does NOT run Ansible itself and
holds NO fiveg-core RBAC. It calls the audited playbooks over MCP
through the Kuadrant-governed gateway (deploy/ocp/rome/
netops-gateway.yaml, mcp-gateway-policies.yaml), presenting its own SA
token. The gateway proves the caller is execution-agent and caps the
rate; the MCP playbook server (deploy/ocp/rome/mcp-playbook.yaml) is the
sole holder of the nf-actuator Role and does the real actuation. The
governance shift is the point: the dangerous capability lives behind a
gate the agent must pass, not in the agent itself.

The approval gate is enforced in code, not in a prompt: if the plan
says approval_required and the incoming A2A message does not carry the
`approve` token, the agent records `awaiting_approval` and refuses to
act. The caller (a human, or an orchestrator relaying a human's
decision) must send `execute loop_id=<id> approve` to proceed.

Every step's playbook run (return code, output tail) and a post-action
snapshot of the fiveg-core deployments are externalized to the state
store and logged to MLflow — the audit record the article requires
from an agent that touches the network.

Runs as deploy/ocp/rome/execution.yaml.
"""
import json
import os
import time
from pathlib import Path

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
# Track 4: Execution reaches the 5G core ONLY through the Kuadrant-
# governed MCP gateway now. This pod holds NO fiveg-core RBAC and runs
# NO Ansible itself. It presents its own ServiceAccount token to the
# gateway, which proves the caller is exactly execution-agent
# (AuthPolicy / Authorino) and caps the actuation rate (RateLimitPolicy
# / Limitador) before the request ever reaches the MCP playbook server —
# the sole holder of the nf-actuator Role. Compromising this agent now
# grants the ability to *ask*, through the gate, not the ability to
# *act*.
GATEWAY_URL = os.environ.get(
    "GATEWAY_URL",
    "http://netops-gateway-data-science-gateway-class."
    "agent-school.svc.cluster.local:8080/mcp")
# The governed catalog, kept here only as a client-side fast-fail. The
# MCP server enforces the authoritative catalog and is the only thing
# that can actuate. `rollback` is the safety action — only the
# Validation agent requests it, and it needs no approval gate: undoing
# is the safe direction.
CATALOG = {"scale_amf", "restart_smf", "rebalance_upf", "rollback"}


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


def _sa_token():
    return Path(SA_DIR + "/token").read_text().strip()


async def _mcp_run_playbook(name):
    """Call run_playbook on the MCP playbook server through the Kuadrant
    gateway, presenting this agent's own SA token as the bearer. The
    gateway's AuthPolicy + RateLimitPolicy gate the call; the MCP server
    does the real, RBAC-backed kubernetes.core actuation and returns the
    post-action NF snapshot."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(
            GATEWAY_URL,
            headers={"Authorization": "Bearer " + _sa_token()},
            timeout=300) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("run_playbook", {"playbook": name})
            return json.loads(res.content[0].text)


def _run_async(coro):
    """Run a coroutine to completion from sync code, even when we are
    already inside the A2A server's event loop, by driving it on its own
    loop in a worker thread."""
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


def run_playbook(name):
    """One governed action — now actuated ONLY through the gateway.
    Execution holds no fiveg-core RBAC and runs no Ansible; it asks the
    MCP playbook server (behind Kuadrant) to run the audited playbook.
    The catalog check here is a client-side fast-fail; the server is
    authoritative."""
    if name not in CATALOG:
        return {"playbook": name, "rc": -1,
                "result": "REFUSED: not in governed catalog (client-side)"}
    t0 = time.time()
    try:
        rec = _run_async(_mcp_run_playbook(name))
    except Exception as e:  # noqa: BLE001
        return {"playbook": name, "rc": -1,
                "seconds": round(time.time() - t0, 1),
                "result": "GATEWAY_ERROR: " + type(e).__name__ + ": "
                          + str(e)[:200]}
    return {"playbook": rec.get("playbook", name),
            "rc": rec.get("rc"),
            "seconds": rec.get("seconds", round(time.time() - t0, 1)),
            "output_tail": rec.get("output_tail"),
            "nf_state_after": rec.get("nf_state_after")}


def execute_plan(loop_id, approved):
    status = STATE.get("loop:" + loop_id + ":status")
    raw = STATE.get("loop:" + loop_id + ":plan")
    if not raw:
        return {"error": "no plan for loop " + loop_id, "status": status}
    if status not in ("planned", "awaiting_approval"):
        return {"error": "loop status is '" + str(status) +
                         "', expected planned/awaiting_approval"}
    plan = json.loads(raw).get("plan", {})
    steps = plan.get("steps", [])

    if plan.get("approval_required") and not approved:
        STATE.set("loop:" + loop_id + ":status", "awaiting_approval")
        return {"gate": "awaiting_approval",
                "reason": "plan.approval_required=true and no approve "
                          "token on the request",
                "steps_pending": len(steps)}

    results = []
    for step in sorted(steps, key=lambda s: s.get("order", 0)):
        results.append(run_playbook(str(step.get("playbook"))))
    # The post-action NF snapshot comes back from the MCP server (which
    # holds the read scope); Execution no longer reads fiveg-core itself.
    snapshot = results[-1].get("nf_state_after") if results else {}
    ok = all(r.get("rc") == 0 for r in results) if results else True
    rec = {"loop_id": loop_id, "stage": "execution",
           "approved_by_caller": approved, "results": results,
           "nf_state_after": snapshot, "all_ok": ok}
    STATE.set("loop:" + loop_id + ":execution", json.dumps(rec))
    STATE.set("loop:" + loop_id + ":status",
              "executed" if ok else "execution_failed")
    return rec


def rollback_plan(loop_id):
    """Safety path: Validation breached the rollback trigger. No
    approval gate — undoing is the safe direction; still audited."""
    status = STATE.get("loop:" + loop_id + ":status")
    if not STATE.get("loop:" + loop_id + ":execution"):
        return {"error": "nothing executed for loop " + loop_id,
                "status": status}
    result = run_playbook("rollback")
    snapshot = result.get("nf_state_after") or {}
    ok = result.get("rc") == 0
    rec = {"loop_id": loop_id, "stage": "rollback",
           "results": [result], "nf_state_after": snapshot, "all_ok": ok}
    STATE.set("loop:" + loop_id + ":rollback", json.dumps(rec))
    STATE.set("loop:" + loop_id + ":status",
              "rolled_back" if ok else "rollback_failed")
    return rec


# ------------------------------------------------------------------- a2a
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.apps import A2AStarletteApplication  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentSkill  # noqa: E402
from a2a.utils import new_agent_text_message  # noqa: E402


class ExecutionExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        text = context.get_user_input() or ""
        toks = text.split()
        loop_id = None
        for tok in toks:
            if tok.startswith("loop_id="):
                loop_id = tok.split("=", 1)[1]
        approved = "approve" in toks
        rollback = "rollback" in toks
        if not loop_id:
            await event_queue.enqueue_event(new_agent_text_message(
                json.dumps({"error": "message must carry loop_id=<id>"})))
            return
        prefix = "rollback-" if rollback else "execution-"
        run_ctx = mlflow.start_run(run_name=prefix + loop_id) \
            if mlflow else None
        try:
            rec = rollback_plan(loop_id) if rollback \
                else execute_plan(loop_id, approved)
            if mlflow:
                try:
                    mlflow.log_param("loop_id", loop_id)
                    if rec.get("gate"):
                        mlflow.log_param("gate", rec["gate"])
                    else:
                        mlflow.log_param("steps_run",
                                         len(rec.get("results", [])))
                        mlflow.log_param("all_ok", rec.get("all_ok"))
                    mlflow.log_dict(rec, "execution.json")
                except Exception as e:
                    print("[mlflow] log skipped:", type(e).__name__, e,
                          flush=True)
        finally:
            if run_ctx:
                run_ctx.__exit__(None, None, None)
        rec["state_key"] = "loop:" + loop_id + \
            (":rollback" if rollback else ":execution")
        await event_queue.enqueue_event(
            new_agent_text_message(json.dumps(rec)))

    async def cancel(self, context, event_queue):
        raise Exception("cancel unsupported")


CARD = AgentCard(
    name="execution",
    description=("Closed-loop NetOps Execution worker: actuates the "
                 "governed remediation plan with real Ansible playbooks "
                 "against the fiveg-core NFs, under a namespace-scoped "
                 "Role and a code-enforced approval gate. No LLM."),
    url=os.environ.get("A2A_PUBLIC_URL",
                       "http://execution-agent.agent-school.svc.cluster.local:8080/"),
    version="0.1.0",
    default_input_modes=["text"], default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(
        id="execute", name="execute",
        description="Execute a planned loop iteration (loop_id=<id>; add "
                    "'approve' to pass the human gate).",
        tags=["netops", "execution", "ansible", "governance"])])

app = A2AStarletteApplication(
    agent_card=CARD,
    http_handler=DefaultRequestHandler(
        agent_executor=ExecutionExecutor(),
        task_store=InMemoryTaskStore())).build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
