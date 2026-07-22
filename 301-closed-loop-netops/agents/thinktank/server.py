"""The MCP think-tank — external reasoning for the 301 closed loop.

The article's architecture keeps "what should we do" separable from
"who is allowed to do it": remediation-flow determination lives in a
reasoning service OUTSIDE the agents' trust domain, reached only over
MCP. On Rome that boundary is modeled as a separate namespace
(`think-tank`) with no shared ServiceAccounts, secrets, or state with
agent-school — the agents know only its URL. Everything the think-tank
decides comes back over the wire and is captured in the caller's audit
trail; the service itself stays a black box to the loop.

Real MCP (streamable-HTTP, python-sdk 1.28.x, stateless): one tool,
`determine_remediation_flow`, which reasons over Diagnostic findings
with the cluster's Kimi-Linear endpoint and returns a strict-JSON flow
built from the governed autonet playbook catalog — the only actions
Execution is allowed to take.

Runs as deploy/ocp/rome/think-tank.yaml.
"""
import json
import os

from mcp.server.fastmcp import FastMCP
from openai import OpenAI

PLAYBOOKS = {
    "scale_amf": "horizontally scale the AMF deployment (adds registration/"
                 "session-setup capacity)",
    "restart_smf": "rolling restart of the SMF (clears stuck PFCP/N4 "
                   "sessions and leaked state)",
    "rebalance_upf": "rebalance UPF load (drains and redistributes user-"
                     "plane tunnels; relieves packet drop/latency)",
}

LLM = OpenAI(base_url=os.environ["LLM_BASE_URL"],
             api_key=os.environ.get("LLM_API_KEY", "none"))
MODEL = os.environ["LLM_MODEL"]

srv = FastMCP(
    "think-tank",
    instructions="External remediation-flow determination for 5G core "
                 "incidents. Input: Diagnostic findings JSON. Output: "
                 "ordered flow over the governed playbook catalog.",
    host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
    stateless_http=True)

PROMPT = """You are an external network-operations reasoning service
(a think-tank) consulted by a closed-loop remediation system for a 5G
core (AMF/SMF/UPF). You are given the Diagnostic agent's findings. Your
job is remediation-FLOW determination only: what the anomaly means and
which governed actions, in which order, would remediate it. You do NOT
execute anything.

The ONLY actions available (the governed playbook catalog):
{catalog}

Findings:
{findings}

Return STRICT JSON only, no prose, with keys:
  meaning (one sentence: what this anomaly pattern means operationally);
  flow (ordered list of objects: {{"order": n, "playbook": <catalog key>,
        "target_nf": "amf"|"smf"|"upf", "reason": short string}});
  risk ("low"|"medium"|"high": blast radius of the flow itself);
  preconditions (list of short strings to verify before acting);
  rollback_trigger (one sentence: the KPI condition that must abort).
Use the minimum flow that plausibly remediates; empty flow if no action
is warranted.
"""


@srv.tool()
def determine_remediation_flow(findings: str) -> str:
    """Turn Diagnostic findings (JSON string) into a remediation-flow
    determination over the governed playbook catalog. Returns JSON."""
    catalog = "\n".join("- " + k + ": " + v for k, v in PLAYBOOKS.items())
    msg = LLM.chat.completions.create(
        model=MODEL, temperature=0.1, timeout=180,
        messages=[{"role": "user", "content": PROMPT.format(
            catalog=catalog, findings=findings)}])
    text = msg.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.strip("`\n")
        if text.startswith("json"):
            text = text[4:]
    try:
        det = json.loads(text)
        for step in det.get("flow", []):
            if step.get("playbook") not in PLAYBOOKS:
                step["playbook_warning"] = "not in governed catalog"
    except Exception:
        det = {"error": "non-json-llm-output", "raw": text[:2000]}
    return json.dumps(det)


if __name__ == "__main__":
    srv.run(transport="streamable-http")
