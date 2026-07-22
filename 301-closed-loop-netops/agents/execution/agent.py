"""301 Execution agent — the governed actuation arm of the loop.

Third worker. Deliberately the least clever component in the system:
no LLM anywhere in this pod. Execution reads the governed plan the
Planning agent published, enforces the approval gate, and actuates the
plan's steps by running the real Ansible playbooks (kubernetes.core)
against the fiveg-core namespace — nothing else, because its
ServiceAccount can touch nothing else (deploy/ocp/rome/
execution-rbac.yaml is the entire actuation surface).

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
import subprocess
import time
from pathlib import Path

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBECONFIG = "/tmp/kubeconfig"
PLAYBOOK_DIR = os.environ.get("PLAYBOOK_DIR", "/playbooks")
CATALOG = {"scale_amf", "restart_smf", "rebalance_upf"}


def write_kubeconfig():
    """Kubeconfig with tokenFile so the SA's rotating bound token is
    always read fresh by ansible's kubernetes client."""
    kc = {
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "in-cluster", "cluster": {
            "server": "https://kubernetes.default.svc",
            "certificate-authority": SA_DIR + "/ca.crt"}}],
        "users": [{"name": "sa", "user": {"tokenFile": SA_DIR + "/token"}}],
        "contexts": [{"name": "in", "context": {
            "cluster": "in-cluster", "user": "sa"}}],
        "current-context": "in",
    }
    Path(KUBECONFIG).write_text(json.dumps(kc))


write_kubeconfig()


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


def nf_snapshot():
    """Post-action view of the fiveg-core deployments (the SA's read
    scope) — replicas + loop annotations, straight from the API."""
    try:
        from kubernetes import client, config
        config.load_incluster_config()
        apps = client.AppsV1Api()
        out = {}
        for d in apps.list_namespaced_deployment("fiveg-core").items:
            ann = (d.spec.template.metadata.annotations or {})
            out[d.metadata.name] = {
                "replicas": d.spec.replicas,
                "ready": d.status.ready_replicas,
                "restartedAt": ann.get("loop.agent-school/restartedAt"),
                "rebalancedAt": ann.get("loop.agent-school/rebalancedAt"),
            }
        return out
    except Exception as e:
        return {"error": type(e).__name__ + ": " + str(e)[:150]}


def run_playbook(name):
    """One governed action. Catalog enforced in code; output captured
    for the audit record."""
    if name not in CATALOG:
        return {"playbook": name, "rc": -1,
                "result": "REFUSED: not in governed catalog"}
    env = dict(os.environ, HOME="/tmp",
               K8S_AUTH_KUBECONFIG=KUBECONFIG,
               ANSIBLE_LOCAL_TEMP="/tmp/.ansible/tmp",
               ANSIBLE_STDOUT_CALLBACK="oneline")
    t0 = time.time()
    proc = subprocess.run(
        ["ansible-playbook", os.path.join(PLAYBOOK_DIR, name + ".yml")],
        capture_output=True, text=True, timeout=420, env=env)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
    return {"playbook": name, "rc": proc.returncode,
            "seconds": round(time.time() - t0, 1), "output_tail": tail}


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
    snapshot = nf_snapshot()
    ok = all(r.get("rc") == 0 for r in results) if results else True
    rec = {"loop_id": loop_id, "stage": "execution",
           "approved_by_caller": approved, "results": results,
           "nf_state_after": snapshot, "all_ok": ok}
    STATE.set("loop:" + loop_id + ":execution", json.dumps(rec))
    STATE.set("loop:" + loop_id + ":status",
              "executed" if ok else "execution_failed")
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
        if not loop_id:
            await event_queue.enqueue_event(new_agent_text_message(
                json.dumps({"error": "message must carry loop_id=<id>"})))
            return
        run_ctx = mlflow.start_run(run_name="execution-" + loop_id) \
            if mlflow else None
        try:
            rec = execute_plan(loop_id, approved)
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
        rec["state_key"] = "loop:" + loop_id + ":execution"
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
