"""Judge smoke (301, Step 3): drive the GenAI plan-judge over A2A.

Sends two contrasting remediation plans and prints the grounded
verdicts. The judge fetches each step's calibrated risk itself (through
the governed remediation-risk scorer tool on /plan-score) and reasons
from it, so a gentle scale-up plan and a restart-heavy plan on a
degraded core should read differently.
"""
import asyncio
import json
import os
from uuid import uuid4

import httpx

JUDGE = os.environ.get(
    "JUDGE_URL",
    "http://plan-judge-agent.agent-school.svc.cluster.local:8080")


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
    {"label": "gentle-scale",
     "steps": [{"action": "scale_amf", "target_nf": "amf",
                "anomaly_score": 0.8}],
     "context": "registration storm on AMF; spare quota to add a replica "
                "(governed cap 5)"},
    {"label": "restart-heavy",
     "steps": [{"action": "restart_smf", "target_nf": "smf",
                "anomaly_score": 0.9},
               {"action": "rebalance_upf", "target_nf": "upf",
                "anomaly_score": 0.6}],
     "context": "SMF session-management failure during evening peak; UPF "
                "already degraded — ordering matters"},
]


async def main():
    async with httpx.AsyncClient(timeout=300) as hc:
        for c in CASES:
            rec = await ask(hc, JUDGE, json.dumps(c))
            if rec.get("error"):
                raise SystemExit("JUDGE_ERROR " + c["label"] + ": "
                                 + json.dumps(rec)[:300])
            v = rec["verdict"]
            print("CASE %s -> decision=%s conf=%s | max_step_risk=%s"
                  % (c["label"], v.get("decision"), v.get("confidence"),
                     rec.get("max_step_risk")), flush=True)
            print("   rationale:", str(v.get("rationale"))[:200], flush=True)
            print("   risks:", v.get("risks"), flush=True)
            assert v.get("decision") in ("accept", "revise", "reject"), \
                "bad decision"
            assert rec.get("quant_scores"), "no grounding scores"
        print("JUDGE_SMOKE_OK", flush=True)


asyncio.run(main())
