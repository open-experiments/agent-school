"""Judge smoke (302, Step 2): drive the GenAI judge over A2A.

Sends two contrasting proposals and prints the grounded verdicts. The
judge fetches the classic score itself (through the governed scorer
tool) and reasons from it, so the two cases should read differently.
"""
import asyncio
import json
import os
from uuid import uuid4

import httpx

JUDGE = os.environ.get(
    "JUDGE_URL",
    "http://judge-agent.agent-school.svc.cluster.local:8080")


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


CASES = [
    {"label": "night-modest",
     "savings_pct": 7.5, "qos_dropped_pct": 0.3,
     "proposal": "sleep 3 of 6 cells 00:00-05:00 (deep night)",
     "context": "deep-night trough, spare capacity to re-home traffic"},
    {"label": "peak-aggressive",
     "savings_pct": 2.0, "qos_dropped_pct": 1.4,
     "proposal": "sleep 5 of 6 cells 19:00-22:00",
     "context": "evening peak; one cell covers a stadium during an event"},
]


async def main():
    async with httpx.AsyncClient(timeout=300) as hc:
        for c in CASES:
            rec = await ask(hc, JUDGE, json.dumps(c))
            if rec.get("error"):
                raise SystemExit("JUDGE_ERROR " + c["label"] + ": "
                                 + json.dumps(rec)[:300])
            v = rec["verdict"]
            q = rec["quant_score"]
            print("CASE %s -> decision=%s conf=%s | score eff=%s fault=%s"
                  % (c["label"], v.get("decision"), v.get("confidence"),
                     q.get("energy_efficiency"), q.get("predicted_fault_rate")),
                  flush=True)
            print("   rationale:", str(v.get("rationale"))[:200], flush=True)
            print("   risks:", v.get("risks"), flush=True)
            assert v.get("decision") in ("accept", "revise", "reject"), \
                "bad decision"
            assert q.get("energy_efficiency") is not None, "no grounding score"
        print("JUDGE_SMOKE_OK", flush=True)


asyncio.run(main())
