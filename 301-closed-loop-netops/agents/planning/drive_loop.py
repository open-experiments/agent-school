"""Actionable-scenario drill (RUNBOOK-quantqual step 6, orchestrator role).

Seeds the loop-state store with a synthetic *diagnosed* record — an AMF
registration storm, the same scenario the course dataset carries — then
drives Planning over A2A so the quant + qual co-decision runs for real:
served risk scores through the governed /plan-score route, judge verdict,
arbiter, MLflow episode record. Marked synthetic_drill=true in the record.
"""
import asyncio
import json
import os
from uuid import uuid4

import httpx
import redis as redislib

PLAN = os.environ.get(
    "PLANNING_URL",
    "http://planning-agent.agent-school.svc.cluster.local:8080")
SCEN = os.environ.get("SCENARIO", "amf_registration_storm")

TELEMETRY = {
    "amf": {"registration_rate_1h_mean": 512.4,
            "registration_success_rate_1h_mean": 86.2,
            "cpu_utilization_1h_mean": 91.7,
            "memory_utilization_1h_mean": 88.9,
            "anomaly_score": -0.62, "anomaly_flag": 1},
    "smf": {"session_establishment_rate_1h_mean": 142.9,
            "session_establishment_success_rate_1h_mean": 93.1,
            "cpu_utilization_1h_mean": 74.3,
            "memory_utilization_1h_mean": 69.0,
            "anomaly_score": -0.18, "anomaly_flag": 1},
    "upf": {"active_sessions_1h_mean": 18234.0,
            "throughput_mbps_1h_mean": 842.1,
            "cpu_utilization_1h_mean": 66.4,
            "memory_utilization_1h_mean": 61.2,
            "anomaly_score": 0.05, "anomaly_flag": 0},
}
FINDINGS = {
    "incident": True,
    "affected_nfs": ["amf", "smf"],
    "severity": "medium",
    "evidence": [
        "amf anomaly_flag=1 score=-0.62; registration_rate_1h_mean 512.4 (~4x baseline); cpu 91.7%",
        "smf anomaly_flag=1 score=-0.18; session establishment success degraded to 93.1%",
        "pattern consistent with amf_registration_storm cascading into SMF session pressure",
    ],
    "hypothesis": "AMF registration storm with cascading SMF session-management pressure",
    "scenario": SCEN,
    "synthetic_drill": True,
}


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
    lid = uuid4().hex[:12]
    state = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                    decode_responses=True)
    rec = {"loop_id": lid, "findings": FINDINGS, "telemetry": TELEMETRY,
           "source": "quantqual-drill"}
    state.set("loop:" + lid + ":diagnostic", json.dumps(rec))
    state.set("loop:" + lid + ":status", "diagnosed")
    state.lpush("loop:index", lid)
    print("DRILL_SEEDED loop", lid, "scenario", SCEN, flush=True)

    async with httpx.AsyncClient(timeout=600) as hc:
        name, planned = await ask(hc, PLAN, "plan loop_id=" + lid)
        if planned.get("error"):
            raise SystemExit("planning error: " + str(planned["error"]))
        print("PLAN_OK", name)
        print(json.dumps(planned["plan"], indent=1))

    prec = json.loads(state.get("loop:" + lid + ":plan"))
    print("CODECISION", json.dumps(prec.get("codecision", {}), indent=1))
    print("STATUS", state.get("loop:" + lid + ":status"))
    print("DRILL_OK", lid)


asyncio.run(main())
