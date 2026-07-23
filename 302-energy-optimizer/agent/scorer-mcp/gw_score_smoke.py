"""Governed-path smoke for the scorer MCP tool (302, Step 1).

Proves the Kuadrant gateway enforces the SAME allow/deny/rate-limit on
*evaluation* (scoring) that 301 proved on *actuation*:

  RUN_SCORE=1 (as energy-optimizer, an allowed identity):
    - raw POST without a token   -> 401/403 (Authorino authn)
    - raw POST with the SA token -> 200 (auth passes)
    - a real MCP score_condition call through the gateway returns the
      classic regressor's energy_efficiency / predicted_fault_rate
    - a burst -> 429 once the RateLimitPolicy window is exhausted

  RUN_SCORE=0 (as any other SA, e.g. diagnostic-agent):
    - raw POST with that valid token -> 403: genuine token, wrong
      identity, so the AuthPolicy rejects it.
"""
import json
import os
import urllib.request
from pathlib import Path

GW = os.environ.get(
    "GATEWAY_URL",
    "http://netops-gateway-data-science-gateway-class."
    "agent-school.svc.cluster.local:8080/score")
TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"
             ).read_text().strip()
INIT = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "score-smoke", "version": "1"}}}).encode()
HDRS = {"Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}


def status(with_token):
    h = dict(HDRS)
    if with_token:
        h["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(GW, data=INIT, headers=h)
    try:
        return urllib.request.urlopen(req, timeout=30).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa: BLE001
        return "ERR:" + type(e).__name__


run_score = os.environ.get("RUN_SCORE", "0") == "1"

if not run_score:
    code = status(with_token=True)
    print("WRONG_SA with valid token -> HTTP", code, flush=True)
    assert code in (401, 403), "expected authz denial, got " + str(code)
    print("AUTHZ_DENY_OK (identity is not an allowed scoring caller)",
          flush=True)
    raise SystemExit(0)

no_tok = status(with_token=False)
print("NO TOKEN -> HTTP", no_tok, flush=True)
assert no_tok in (401, 403), "expected authn denial, got " + str(no_tok)
print("AUTHN_DENY_OK", flush=True)

with_tok = status(with_token=True)
print("WITH energy-optimizer token -> HTTP", with_tok, flush=True)
assert with_tok == 200, "expected 200 through gate, got " + str(with_tok)
print("AUTH_ALLOW_OK", flush=True)

import asyncio  # noqa: E402


async def score():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(
            GW, headers={"Authorization": "Bearer " + TOKEN},
            timeout=120) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS", [t.name for t in tools.tools], flush=True)
            res = await s.call_tool("score_condition",
                                    {"savings_pct": 7.5,
                                     "qos_dropped_pct": 0.3})
            return json.loads(res.content[0].text)


rec = asyncio.run(score())
print("SCORE_OK energy_efficiency=%s predicted_fault_rate=%s" % (
    rec.get("energy_efficiency"), rec.get("predicted_fault_rate")), flush=True)
assert "energy_efficiency" in rec, "scorer returned no efficiency"

# rate limit: each status() is one raw request; burst above 60/60s.
codes = [status(with_token=True) for _ in range(70)]
n429 = sum(1 for c in codes if c == 429)
print("RATE burst 429 count:", n429, flush=True)
assert n429 > 0, "expected a 429 once the window was exhausted"
print("RATE_LIMIT_OK (%d/%d throttled)" % (n429, len(codes)), flush=True)
print("SCORE_GATEWAY_SMOKE_OK", flush=True)
