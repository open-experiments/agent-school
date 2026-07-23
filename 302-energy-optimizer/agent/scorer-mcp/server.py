"""Governed scorer MCP tool — the classic ML outcome, behind the gate.

302's energy optimizer decides with two complementary signals: a
calibrated *quantitative* signal from the classic sustainability
regressor (served on KServe), and a *qualitative* verdict from a GenAI
judge. This server is the quantitative half, wrapped as a single MCP
tool so the judge reaches the classic model ONLY through the Kuadrant
gateway — the same East-West governance 301 puts on actuation
(AuthPolicy: who may score; RateLimitPolicy: how often), now applied to
evaluation. The regressor stays exactly what it was; we only change how
it is reached.

Tool:
  score_condition(savings_pct, qos_dropped_pct) -> the regressor's
    energy_efficiency + predicted_fault_rate for the simulated network
    condition, plus the KPI row scored (the documented modeling bridge:
    dataset-median KPIs with the sim's QoS impact applied on top).

Real MCP (streamable-HTTP, python-sdk 1.28.x, stateless), mounted at
/score so it can share the netops-gateway with the 301 playbook tool
(which owns /mcp). Runs as deploy/ocp/rome/scorer-mcp.yaml.
"""
import json
import os
import urllib.request

from mcp.server.fastmcp import FastMCP

SCORER_URL = os.environ.get(
    "SCORER_URL",
    "http://sustainability-scorer-predictor.agent-school."
    "svc.cluster.local:8080")

# Dataset-median KPI row (medians of the published 100K-row 5G netops
# dataset); the sim's QoS impact is applied on top. Kept identical to
# the optimizer's BASELINE_KPIS so the governed tool scores exactly what
# the in-process bridge used to.
BASELINE_KPIS = {
    "Cell Availability (%)": 98.6, "MTTR (hours)": 3.4,
    "Throughput (Mbps)": 500.0, "Latency (ms)": 50.0,
    "Packet Loss Rate (%)": 2.0, "Call Drop Rate (%)": 1.0,
    "Handover Success Rate (%)": 97.0, "Alarm Count": 12,
    "Critical Alarm Count": 3, "Temperature (°C)": 15.0,
    "Humidity (%)": 55.0,
}

srv = FastMCP(
    "sustainability-scorer",
    instructions="The calibrated, quantitative half of the energy "
                 "optimizer's decision: scores a simulated network "
                 "condition on the classic sustainability regressor.",
    host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
    stateless_http=True, streamable_http_path="/score")


def _score(savings_pct, qos_dropped_pct):
    kpis = dict(BASELINE_KPIS)
    drop = float(qos_dropped_pct)
    kpis["Packet Loss Rate (%)"] = round(
        kpis["Packet Loss Rate (%)"] + drop, 3)
    kpis["Call Drop Rate (%)"] = round(
        kpis["Call Drop Rate (%)"] + 0.5 * drop, 3)
    inputs = []
    for k, v in kpis.items():
        dt = "INT64" if isinstance(v, int) else "FP64"
        inputs.append({"name": k, "shape": [1, 1], "datatype": dt,
                       "data": [v]})
    body = json.dumps({"parameters": {"content_type": "pd"},
                       "inputs": inputs}).encode()
    req = urllib.request.Request(
        SCORER_URL + "/v2/models/sustainability-scorer/infer",
        data=body, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    out = {o["name"]: o["data"][0] for o in resp.get("outputs", [])}
    return {"energy_efficiency": round(float(out["energy_efficiency"]), 2),
            "predicted_fault_rate": round(
                float(out["predicted_fault_rate"]), 2),
            "kpi_row_used": kpis}


@srv.tool()
def score_condition(savings_pct: float, qos_dropped_pct: float) -> str:
    """Score a simulated network condition on the classic sustainability
    regressor. Provide the simulation's savings_pct and qos_dropped_pct;
    returns energy_efficiency and predicted_fault_rate (the calibrated
    quantitative signal) plus the KPI row scored. This is the number the
    GenAI judge must reason FROM — it does not invent scores."""
    try:
        rec = _score(savings_pct, qos_dropped_pct)
        rec["savings_pct"] = float(savings_pct)
        rec["qos_dropped_pct"] = float(qos_dropped_pct)
        return json.dumps(rec)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": type(e).__name__ + ": " + str(e)[:200]})


if __name__ == "__main__":
    srv.run(transport="streamable-http")
