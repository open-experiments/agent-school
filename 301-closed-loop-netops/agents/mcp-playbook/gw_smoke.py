"""Governed-path smoke — proves the Kuadrant gateway actually enforces.

Runs against the NetOps gateway
(http://netops-gateway-...agent-school.svc:8080/mcp). Two roles:

  RUN_ACTUATION=1 (as execution-agent, the allowed identity):
    - raw POST WITHOUT a token   -> expect 401/403 (Authorino authn)
    - raw POST WITH the SA token -> expect 200 (auth passes)
    - real MCP call through the gateway with the token: run_playbook
      restart_smf -> the playbook actually runs against fiveg-core, but
      only because the request cleared the gate
    - burst of requests -> expect 429 to appear once the RateLimitPolicy
      window is exhausted (Limitador)

  RUN_ACTUATION=0 (as any other SA, e.g. diagnostic-agent):
    - raw POST WITH that SA's valid token -> expect 403: the token is
      genuine but the identity is not execution-agent, so the
      AuthPolicy authorization rule rejects it. Compromising a
      different agent does not grant actuation.
"""
import json
import os
import time
import urllib.request
from pathlib import Path

GW = os.environ.get(
    "GATEWAY_URL",
    "http://netops-gateway-data-science-gateway-class."
    "agent-school.svc.cluster.local:8080/mcp")
TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"
             ).read_text().strip()
INIT = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "gw-smoke", "version": "1"}}}).encode()
HDRS = {"Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}


def status(with_token):
    h = dict(HDRS)
    if with_token:
        h["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(GW, data=INIT, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return "ERR:" + type(e).__name__


run_actuation = os.environ.get("RUN_ACTUATION", "0") == "1"

if not run_actuation:
    code = status(with_token=True)
    print("WRONG_SA with valid token -> HTTP", code, flush=True)
    assert code in (401, 403), "expected authz denial, got " + str(code)
    print("AUTHZ_DENY_OK (identity is not execution-agent)", flush=True)
    raise SystemExit(0)

# execution-agent path -------------------------------------------------
no_tok = status(with_token=False)
print("NO TOKEN -> HTTP", no_tok, flush=True)
assert no_tok in (401, 403), "expected authn denial, got " + str(no_tok)
print("AUTHN_DENY_OK", flush=True)

with_tok = status(with_token=True)
print("WITH execution-agent token -> HTTP", with_tok, flush=True)
assert with_tok == 200, "expected 200 through gate, got " + str(with_tok)
print("AUTH_ALLOW_OK", flush=True)

# real governed actuation through the gateway --------------------------
import asyncio  # noqa: E402


async def actuate():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(
            GW, headers={"Authorization": "Bearer " + TOKEN},
            timeout=300) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS", [t.name for t in tools.tools], flush=True)
            res = await s.call_tool("run_playbook",
                                    {"playbook": "restart_smf"})
            return json.loads(res.content[0].text)


rec = asyncio.run(actuate())
print("ACTUATE_OK rc=%s nf=%s" % (
    rec.get("rc"), json.dumps(rec.get("nf_state_after"))[:200]), flush=True)
assert rec.get("rc") == 0, "playbook failed through gateway"

# rate limit -----------------------------------------------------------
# Each status() call is a single raw request, so burst above the
# actuation cap (30/60s) to force Limitador to throttle the tail.
codes = []
for i in range(40):
    codes.append(status(with_token=True))
n429 = sum(1 for c in codes if c == 429)
print("RATE burst codes:", codes, flush=True)
assert n429 > 0, "expected a 429 once the window was exhausted"
print("RATE_LIMIT_OK (%d/%d requests throttled)" % (n429, len(codes)),
      flush=True)
print("GATEWAY_SMOKE_OK", flush=True)
