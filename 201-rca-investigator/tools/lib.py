"""Skills for the RCA Investigator.

Thin clients only: retrieval calls the RAG service backend over HTTP
(pattern 2), and the incident feed reads the same real alerts.json the 101
course ships. Exposed in-process (agent) and as an MCP server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

RAG_URL = os.environ.get("RAG_URL", "http://127.0.0.1:8201")
DATA_DIR = Path(os.environ.get(
    "DATA_DIR",
    Path(__file__).resolve().parent.parent.parent / "101-noc-assistant" / "data",
))


def get_incident(component: str = "all") -> str:
    """Return the alert feed entries (type, severity, window, KPI deltas)."""
    raw = json.loads((DATA_DIR / "alerts.json").read_text())
    alerts = []
    for a in raw.get("alerts", []):
        if component.lower() != "all" and a.get("component", "").lower() != component.lower():
            continue
        deltas = a.get("metrics_snapshot", {}).get("delta_percent", {})
        alerts.append({
            "type": a.get("type"), "severity": a.get("severity"),
            "component": a.get("component"), "description": a.get("description"),
            "start_time": a.get("start_time"), "end_time": a.get("end_time"),
            "kpi_delta_percent": {k: round(v, 1) for k, v in deltas.items() if abs(v) >= 1.0},
        })
    return json.dumps({"total": len(alerts), "alerts": alerts})


def retrieve_evidence(query: str, nf: str = "all", k: int = 5) -> str:
    """Query the RAG service backend for telemetry/alert records."""
    r = httpx.get(f"{RAG_URL}/search", params={"q": query, "nf": nf, "k": k}, timeout=20)
    r.raise_for_status()
    return json.dumps(r.json())


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_incident",
            "description": "Fetch the structured alert feed for a component (AMF|SMF|UPF|all).",
            "parameters": {
                "type": "object",
                "properties": {"component": {"type": "string", "enum": ["AMF", "SMF", "UPF", "all"]}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_evidence",
            "description": "Retrieve telemetry and alert records from the RAG backend. Cite returned record ids in the report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "nf": {"type": "string", "enum": ["amf", "smf", "upf", "all"]},
                    "k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_HANDLERS = {
    "get_incident": get_incident,
    "retrieve_evidence": retrieve_evidence,
}
