"""Governed remediation-risk scorer MCP tool — 301's quantitative half.

The Planning quant+qual co-decision needs a calibrated number BEFORE it
commits a remediation plan. This server is that number, wrapped as a
single MCP tool so both the Planning agent and the GenAI judge reach the
classic model ONLY through the Kuadrant gateway — the very same
East-West governance 301 already puts on actuation (AuthPolicy: WHO may
score; RateLimitPolicy: HOW OFTEN), now applied to the decision itself.
The served regressor stays exactly what it is; we only change how it is
reached.

Tool:
  score_remediation(action, target_nf, anomaly_score, utilization=None,
                    severity=None) -> the served model's calibrated risk
    (0..1) and band (low|medium|high) for actuating `action` on
    `target_nf` under the given incident state, plus the feature row
    scored.

The documented BRIDGE (incident state -> model feature row) lives here,
server-side, so every consumer scores through the same governed path
with the same bridge — mirroring 302's scorer tool:
  - severity: if not supplied, taken from the NF's live anomaly_score
    (101's Feast verdict is a calibrated 0..1 severity signal).
  - utilization: if not supplied, a documented per-NF dataset-median
    load is used (the training medians), scaled up by the anomaly
    signal — an incident implies elevated load. Callers with a live KPI
    reading pass `utilization` explicitly and override the default.

Real MCP (streamable-HTTP, python-sdk 1.28.x, stateless), mounted at
/plan-score so it shares 301's netops-gateway with the playbook tool
(which owns /mcp) and the 302 scorer (/score). Runs as
deploy/ocp/rome/scorer-mcp.yaml.
"""
import json
import os
import urllib.request

from mcp.server.fastmcp import FastMCP

SCORER_URL = os.environ.get(
    "SCORER_URL",
    "http://netops-remediation-risk-predictor.agent-school."
    "svc.cluster.local:8080")

# The real playbook catalog + intrinsic disruptiveness — kept identical
# to training/train_remediation_risk.py so the tool builds exactly the
# feature the model was trained on.
ACTIONS = {
    "scale_amf":     {"nf": "amf", "base": 0.15},
    "rebalance_upf": {"nf": "upf", "base": 0.45},
    "rollback":      {"nf": "any", "base": 0.60},
    "restart_smf":   {"nf": "smf", "base": 0.80},
}
# Per-NF dataset-median utilization (the training medians); the incident
# signal is applied on top. Documented bridge, same idea as 302's
# BASELINE_KPIS.
MEDIAN_UTIL = {"amf": 0.42, "smf": 0.38, "upf": 0.45}

srv = FastMCP(
    "remediation-risk-scorer",
    instructions="The calibrated, quantitative half of the closed-loop "
                 "Planning decision: scores how risky a proposed "
                 "remediation action is under the current incident state.",
    host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
    stateless_http=True, streamable_http_path="/plan-score")


def _feature_row(action, target_nf, anomaly_score, utilization, severity):
    meta = ACTIONS[action]
    nf = target_nf if meta["nf"] == "any" else meta["nf"]
    sev = float(severity) if severity is not None \
        else float(anomaly_score) * 2.0        # 0..1 anomaly -> 0..2 sev
    if utilization is not None:
        util = float(utilization)
    else:
        # incident implies elevated load over the NF's median
        util = min(MEDIAN_UTIL.get(nf, 0.42) * (1.0 + float(anomaly_score)),
                   1.0)
    return {
        "action_base": meta["base"],
        "target_util": util, "target_headroom": 1.0 - util,
        "severity": sev,
        "cpu": util, "mem": util, "buffer": util if nf == "upf" else 0.0,
        "health_ratio": max(0.0, 1.0 - sev / 2.0),
        "is_amf": int(nf == "amf"), "is_smf": int(nf == "smf"),
        "is_upf": int(nf == "upf"),
        "act_scale_amf": int(action == "scale_amf"),
        "act_rebalance_upf": int(action == "rebalance_upf"),
        "act_rollback": int(action == "rollback"),
        "act_restart_smf": int(action == "restart_smf"),
    }, nf


def _score(action, target_nf, anomaly_score, utilization, severity):
    row, nf = _feature_row(action, target_nf, anomaly_score,
                           utilization, severity)
    inputs = []
    for k, v in row.items():
        dt = "INT64" if isinstance(v, int) else "FP64"
        inputs.append({"name": k, "shape": [1, 1], "datatype": dt,
                       "data": [v]})
    body = json.dumps({"parameters": {"content_type": "pd"},
                       "inputs": inputs}).encode()
    req = urllib.request.Request(
        SCORER_URL + "/v2/models/remediation-risk-scorer/infer",
        data=body, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    out = {o["name"]: o["data"][0] for o in resp.get("outputs", [])}
    return {"risk": round(float(out["risk"]), 3),
            "risk_band": str(out["risk_band"]),
            "target_nf": nf, "feature_row": row}


@srv.tool()
def score_remediation(action: str, target_nf: str, anomaly_score: float,
                      utilization: float = None,
                      severity: float = None) -> str:
    """Score the RISK of actuating `action` (scale_amf | rebalance_upf |
    restart_smf | rollback) on `target_nf` (amf|smf|upf) under the
    current incident state (`anomaly_score` 0..1 from the Diagnostic /
    Feast verdict; optional live `utilization` 0..1 and `severity`).
    Returns the served model's calibrated risk 0..1 and band
    (low|medium|high) plus the feature row scored. This is the number the
    Planning gate and the GenAI judge must reason FROM — neither invents
    a risk."""
    try:
        if action not in ACTIONS:
            return json.dumps({"error": "unknown action: " + str(action)})
        rec = _score(action, target_nf, anomaly_score, utilization, severity)
        rec["action"] = action
        rec["anomaly_score"] = float(anomaly_score)
        return json.dumps(rec)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": type(e).__name__ + ": " + str(e)[:200]})


if __name__ == "__main__":
    srv.run(transport="streamable-http")
