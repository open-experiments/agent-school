"""Skill logic for the NOC Assistant.

Works on the real Telco-AIX 5gprod dataset (https://huggingface.co/datasets/fenar/5gcore-prod):
per-minute AMF/SMF/UPF KPI series plus a structured alert feed. Anomaly
detection uses Isolation Forest, the same algorithm the source experiment
uses, so the agent's tools behave like the original NOC pipeline.

These functions are the single source of truth for what the agent can do.
They are exposed two ways: in-process (agent/noc_agent.py registers them as
tool-call handlers) and as MCP servers (telemetry_mcp.py, runbook_mcp.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from sklearn.ensemble import IsolationForest

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RUNBOOK_DIR = BASE_DIR / "runbooks"

NF_FILES = {
    "amf": DATA_DIR / "amf_metrics.csv",
    "smf": DATA_DIR / "smf_metrics.csv",
    "upf": DATA_DIR / "upf_metrics.csv",
}


def _load(nf: str) -> pd.DataFrame:
    path = NF_FILES[nf]
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; copy the 5gprod dataset into data/")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return df.sort_values("timestamp")


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c != "timestamp"]


def get_kpi_summary(nf: str = "all", window_minutes: int = 60) -> str:
    """Summarize KPIs for amf|smf|upf|all over the last window of the series."""
    nfs = list(NF_FILES) if nf.lower() == "all" else [nf.lower()]
    if any(n not in NF_FILES for n in nfs):
        return json.dumps({"error": f"unknown nf '{nf}', use amf|smf|upf|all"})
    out = {}
    for n in nfs:
        df = _load(n)
        cutoff = df["timestamp"].max() - pd.Timedelta(minutes=window_minutes)
        win = df[df["timestamp"] >= cutoff]
        out[n] = {
            col: {
                "mean": round(float(win[col].mean()), 2),
                "min": round(float(win[col].min()), 2),
                "max": round(float(win[col].max()), 2),
            }
            for col in _numeric_cols(df)
        }
    return json.dumps({"window_minutes": window_minutes, "summary": out})


def detect_anomalies(nf: str = "all", contamination: float = 0.02) -> str:
    """Isolation Forest anomaly detection per NF, as in the source experiment.

    Returns the anomalous timestamps with the KPIs that deviate most from
    that NF's mean (top 3 per row), most recent first.
    """
    nfs = list(NF_FILES) if nf.lower() == "all" else [nf.lower()]
    if any(n not in NF_FILES for n in nfs):
        return json.dumps({"error": f"unknown nf '{nf}', use amf|smf|upf|all"})
    findings = []
    for n in nfs:
        df = _load(n)
        cols = _numeric_cols(df)
        model = IsolationForest(contamination=contamination, random_state=42)
        flags = model.fit_predict(df[cols])
        means, stds = df[cols].mean(), df[cols].std().replace(0, 1.0)
        for _, row in df[flags == -1].iterrows():
            z = ((row[cols] - means) / stds).astype(float)
            top = z.abs().sort_values(ascending=False).head(3)
            findings.append(
                {
                    "timestamp": str(row["timestamp"]),
                    "nf": n,
                    "deviating_kpis": {
                        k: {"value": round(float(row[k]), 2), "zscore": round(float(z[k]), 2)}
                        for k in top.index
                    },
                }
            )
    findings.sort(key=lambda f: f["timestamp"], reverse=True)
    return json.dumps({"contamination": contamination, "anomalies": findings[:20]})


def get_active_alerts(component: str = "all") -> str:
    """Return the alert feed (type, severity, component, window, KPI deltas)."""
    raw = json.loads((DATA_DIR / "alerts.json").read_text())
    alerts = []
    for a in raw.get("alerts", []):
        if component.lower() != "all" and a.get("component", "").lower() != component.lower():
            continue
        deltas = a.get("metrics_snapshot", {}).get("delta_percent", {})
        moved = {k: round(v, 1) for k, v in deltas.items() if abs(v) >= 1.0}
        alerts.append(
            {
                "type": a.get("type"),
                "severity": a.get("severity"),
                "component": a.get("component"),
                "description": a.get("description"),
                "start_time": a.get("start_time"),
                "end_time": a.get("end_time"),
                "kpi_delta_percent": moved,
            }
        )
    return json.dumps({"total": len(alerts), "alerts": alerts})


def search_runbooks(query: str, top_k: int = 2) -> str:
    """Keyword-score runbook markdown files and return the best matches."""
    terms = [t for t in re.findall(r"[a-z0-9_]+", query.lower()) if len(t) > 2]
    scored = []
    for path in sorted(RUNBOOK_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        score = sum(text.lower().count(t) for t in terms)
        if score > 0:
            scored.append((score, path.name, text))
    scored.sort(reverse=True)
    results = [
        {"runbook": name, "score": score, "content": text[:1500]}
        for score, name, text in scored[:top_k]
    ]
    return json.dumps({"query": query, "results": results})


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_kpi_summary",
            "description": "Summarize 5G core KPIs for a network function (amf|smf|upf|all) over the last N minutes of the series.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nf": {"type": "string", "enum": ["amf", "smf", "upf", "all"]},
                    "window_minutes": {"type": "integer", "default": 60},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_anomalies",
            "description": "Run Isolation Forest anomaly detection on AMF/SMF/UPF KPI series and return anomalous timestamps with their most deviating KPIs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nf": {"type": "string", "enum": ["amf", "smf", "upf", "all"]},
                    "contamination": {"type": "number", "default": 0.02},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_alerts",
            "description": "Fetch the structured alert feed: type, severity, component, time window, and KPI percentage deltas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component": {"type": "string", "enum": ["AMF", "SMF", "UPF", "all"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_runbooks",
            "description": "Search operational runbooks for remediation guidance by alert type or symptom.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 2},
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_HANDLERS = {
    "get_kpi_summary": get_kpi_summary,
    "detect_anomalies": detect_anomalies,
    "get_active_alerts": get_active_alerts,
    "search_runbooks": search_runbooks,
}
