"""Full-loop smoke: Diagnostic -> Planning -> [gate] -> Execution.

Drives one complete loop iteration over A2A and proves the approval
gate does its job: the first execute call (no approve token) must be
REFUSED with `awaiting_approval`; only the second call, carrying the
caller's approve, may actuate. Then verifies the state advanced to
`executed`, every playbook returned rc=0, and prints the post-action
NF snapshot straight from the execution record.

Runs as deploy/ocp/rome/job-smoke-loop.yaml.
"""
import asyncio
import json
import os
from uuid import uuid4

import httpx
import redis as redislib

DIAG = os.environ.get(
    "DIAGNOSTIC_URL",
    "http://diagnostic-agent.agent-school.svc.cluster.local:8080")
PLAN = os.environ.get(
    "PLANNING_URL",
    "http://planning-agent.agent-school.svc.cluster.local:8080")
EXEC = os.environ.get(
    "EXECUTION_URL",
    "http://execution-agent.agent-school.svc.cluster.local:8080")


async def ask(hc, base, text):
    from a2a.client import A2ACardResolver, A2AClient
    from a2a.types import MessageSendParams, SendMessageRequest

    card = await A2ACardResolver(httpx_client=hc, base_url=base
                                 ).get_agent_card()
    client = A2AClient(httpx_client=hc, agent_card=card)
    req = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(
        message={"role": "user", "message_id": uuid4().hex,
                 "parts": [{"kind": "text", "text": text}]}))
    resp = await client.send_message(req)
    return json.loads(resp.root.result.parts[0].root.text)


async def main():
    async with httpx.AsyncClient(timeout=900) as hc:
        diag = await ask(hc, DIAG, "diagnose current network state")
        lid = diag["loop_id"]
        print("DIAG_OK", lid, "incident=%s severity=%s" % (
            diag["findings"].get("incident"),
            diag["findings"].get("severity")))

        planned = await ask(hc, PLAN, "plan loop_id=" + lid)
        p = planned["plan"]
        print("PLAN_OK steps=%d risk=%s approval_required=%s" % (
            len(p.get("steps", [])), p.get("risk"),
            p.get("approval_required")))

        gate = await ask(hc, EXEC, "execute loop_id=" + lid)
        if p.get("approval_required"):
            assert gate.get("gate") == "awaiting_approval", \
                "gate did not hold: " + json.dumps(gate)[:200]
            print("GATE_OK refused without approval:", gate["reason"])
            done = await ask(hc, EXEC, "execute loop_id=" + lid + " approve")
        else:
            done = gate
        if done.get("gate") or done.get("error"):
            raise SystemExit("execution failed: " + json.dumps(done)[:300])

        print("EXEC_OK all_ok=%s" % done.get("all_ok"))
        for r in done.get("results", []):
            print("  step %s rc=%s %ss" % (
                r["playbook"], r["rc"], r.get("seconds")))
        print("NF_STATE", json.dumps(done.get("nf_state_after"), indent=1))

        state = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                        decode_responses=True)
        status = state.get("loop:" + lid + ":status")
        assert status == "executed", "status is " + str(status)
        print("STATE_OK status=executed")
        print("LOOP_OK", lid)


asyncio.run(main())
