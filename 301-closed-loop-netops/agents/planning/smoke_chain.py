"""Chain smoke test: Diagnostic -> Planning over A2A, one loop id.

Plays the orchestrator role for one loop iteration: asks Diagnostic to
diagnose (minting the loop id), hands the same loop id to Planning
(which reads Diagnostic's record from the state store and consults the
MCP think-tank across the namespace boundary), then verifies the
externalized state advanced diagnosed -> planned and prints the plan.

Runs as deploy/ocp/rome/job-smoke-chain.yaml.
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
    return card.name, json.loads(resp.root.result.parts[0].root.text)


async def main():
    async with httpx.AsyncClient(timeout=600) as hc:
        name, diag = await ask(hc, DIAG, "diagnose current network state")
        lid = diag["loop_id"]
        print("DIAG_OK", name, "loop", lid,
              "incident=" + str(diag["findings"].get("incident")),
              "severity=" + str(diag["findings"].get("severity")))

        name, planned = await ask(hc, PLAN, "plan loop_id=" + lid)
        if planned.get("error"):
            raise SystemExit("planning error: " + str(planned["error"]))
        print("PLAN_OK", name)
        print(json.dumps(planned["plan"], indent=1))

        state = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                        decode_responses=True)
        status = state.get("loop:" + lid + ":status")
        rec = json.loads(state.get("loop:" + lid + ":plan"))
        print("STATE_OK status=" + status,
              "determination_meaning=" +
              str(rec["determination"].get("meaning", ""))[:100])
        assert status == "planned", "status did not advance"
        print("CHAIN_OK", lid)


asyncio.run(main())
