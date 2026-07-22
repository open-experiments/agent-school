"""A2A smoke client for the Diagnostic agent — proof, not vibes.

Resolves the agent card from /.well-known/agent.json, sends a real
message/send over JSON-RPC, prints the findings the agent returns, then
reads the externalized state back from the workflow store to prove the
12-Factor discipline holds (the answer and the state are two different
things, and the state survives the agent).

Runs as deploy/ocp/rome/job-smoke-diagnostic.yaml.
"""
import asyncio
import json
import os
from uuid import uuid4

import httpx
import redis as redislib

BASE = os.environ.get(
    "DIAGNOSTIC_URL",
    "http://diagnostic-agent.agent-school.svc.cluster.local:8080")


async def main():
    from a2a.client import A2ACardResolver, A2AClient
    from a2a.types import MessageSendParams, SendMessageRequest

    async with httpx.AsyncClient(timeout=300) as hc:
        card = await A2ACardResolver(httpx_client=hc, base_url=BASE
                                     ).get_agent_card()
        print("CARD", card.name, "-", card.description[:80])
        client = A2AClient(httpx_client=hc, agent_card=card)
        req = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(
            message={"role": "user", "message_id": uuid4().hex,
                     "parts": [{"kind": "text",
                                "text": "diagnose current network state"}]}))
        resp = await client.send_message(req)
        part = resp.root.result.parts[0].root.text
        payload = json.loads(part)
        print("A2A_RESPONSE")
        print(json.dumps(payload, indent=1))

        state = redislib.Redis.from_url(os.environ["STATE_STORE_URL"],
                                        decode_responses=True)
        key = payload["state_key"]
        rec = json.loads(state.get(key))
        print("STATE_OK", key,
              "status=" + state.get("loop:" + payload["loop_id"] + ":status"))
        print("state findings severity:",
              rec["findings"].get("severity"))
        print("SMOKE_OK")


asyncio.run(main())
