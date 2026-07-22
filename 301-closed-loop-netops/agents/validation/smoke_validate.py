"""Validation smoke — both dispositions of the loop's final stage.

Path A (natural): validate the most recent `executed` loop. On Rome
the published telemetry is a fixed dataset, so the online verdicts do
not react to the stand-in NFs being scaled — the honest expected
verdict is `stable` and the loop closes as `validated_monitor`.

Path B (rollback drill): a synthetic loop record — clearly labeled,
written by this harness — whose pre-action baseline is healthy
(anomaly_score 0.5, flags 0). Against the real post-action verdicts
(~ -0.02, flags 1) that reads as deterioration, so Validation must
request a REAL rollback through Execution: amf returns to baseline 2
replicas on the actual cluster. This is a drill of the safety arm,
not a simulated result — the playbook really runs.

Runs as deploy/ocp/rome/job-smoke-validate.yaml.
"""
import asyncio
import json
import os
from uuid import uuid4

import httpx
import redis as redislib

VALID = os.environ.get(
    "VALIDATION_URL",
    "http://validation-agent.agent-school.svc.cluster.local:8080")

STATE = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                decode_responses=True)


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


def latest_executed():
    for lid in STATE.lrange("loop:index", 0, 20):
        if STATE.get("loop:" + lid + ":status") == "executed":
            return lid
    return None


async def main():
    async with httpx.AsyncClient(timeout=900) as hc:
        # ---- Path A: natural validation of the last executed loop
        lid = latest_executed()
        assert lid, "no executed loop found - run job-smoke-loop first"
        rec = await ask(hc, VALID, "validate loop_id=" + lid)
        print("PATH_A loop", lid, "verdict=%s disposition=%s" % (
            rec.get("verdict"), rec.get("disposition")))
        print(" deltas:", json.dumps(rec.get("score_deltas")))
        status = STATE.get("loop:" + lid + ":status")
        assert status in ("validated_monitor", "validated_resolved"), status
        print("PATH_A_OK status=" + status)

        # ---- Path B: rollback drill (synthetic healthy baseline)
        drill = "drill" + uuid4().hex[:8]
        pre = {nf: {"anomaly_score": 0.5, "anomaly_flag": 0.0}
               for nf in ("amf", "smf", "upf")}
        STATE.set("loop:" + drill + ":diagnostic", json.dumps({
            "loop_id": drill, "stage": "diagnostic",
            "note": "ROLLBACK DRILL - synthetic healthy baseline "
                    "written by smoke_validate.py",
            "telemetry": pre, "findings": {"incident": False}}))
        STATE.set("loop:" + drill + ":plan", json.dumps({
            "loop_id": drill, "stage": "planning",
            "plan": {"steps": [{"order": 1, "playbook": "scale_amf",
                                "target_nf": "amf",
                                "expected_impact": "drill"}],
                     "risk": "low", "approval_required": False,
                     "rollback": {"trigger": "any anomaly-score drop > "
                                             "0.05 vs baseline",
                                  "action": "rollback"}}}))
        STATE.set("loop:" + drill + ":execution", json.dumps({
            "loop_id": drill, "stage": "execution",
            "note": "ROLLBACK DRILL record", "results": [], "all_ok": True}))
        STATE.set("loop:" + drill + ":status", "executed")
        STATE.lpush("loop:index", drill)

        rec = await ask(hc, VALID, "validate loop_id=" + drill)
        print("PATH_B drill", drill, "verdict=%s" % rec.get("verdict"))
        assert rec.get("verdict") == "deteriorated", json.dumps(rec)[:300]
        rb = rec.get("rollback", {})
        assert rb.get("all_ok"), "rollback failed: " + json.dumps(rb)[:300]
        amf = (rb.get("nf_state_after") or {}).get("amf", {})
        print("ROLLBACK_OK amf replicas ->", amf.get("replicas"))
        status = STATE.get("loop:" + drill + ":status")
        assert status == "rolled_back", status
        print("PATH_B_OK status=rolled_back")
        print("VALIDATE_OK")


asyncio.run(main())
