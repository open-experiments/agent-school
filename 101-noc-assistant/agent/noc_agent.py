"""NOC Assistant: a single-agent tool-calling loop.

Runs against any OpenAI-compatible endpoint (vLLM on RHOAI, MaaS, Llama
Stack's compatible API). The harness is deliberately small so the loop is
readable: plan -> tool call -> observe -> repeat -> answer.

Offline mode (--offline) replays a scripted episode with no endpoint, so the
loop mechanics and traces can be studied before any model is involved.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.lib import TOOL_HANDLERS, TOOL_SCHEMAS  # noqa: E402

SYSTEM_PROMPT = (
    "You are a NOC assistant for a 5G core (AMF, SMF, UPF). "
    "Use tools to inspect telemetry and runbooks before answering. "
    "Ground every claim in tool output; never invent KPI values. "
    "Finish with: findings, probable cause, and recommended next action."
)

MAX_TURNS = 6


def trace(step: str, detail: str) -> None:
    print(f"[trace] {time.strftime('%H:%M:%S')} {step:<12} {detail}")


def run_tool(name: str, args: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"})
    trace("tool-call", f"{name}({json.dumps(args)})")
    result = handler(**args)
    trace("tool-result", result[:160] + ("..." if len(result) > 160 else ""))
    return result


def _make_client():
    """OpenAI client; with LLM_WIRE_LOG set, every HTTP request/response to
    the model endpoint is appended to that file as JSONL (auth redacted).
    Used by the QA evidence packs under QA/."""
    from openai import OpenAI  # imported lazily so offline mode needs no SDK

    base = dict(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ.get("LLM_API_KEY", "none"),
    )
    wire_path = os.environ.get("LLM_WIRE_LOG")
    if not wire_path:
        return OpenAI(**base)

    import httpx

    wire = open(wire_path, "a", encoding="utf-8")

    def _redact(headers):
        return {
            k: ("***REDACTED***" if k.lower() == "authorization" else v)
            for k, v in headers.items()
        }

    def on_request(request: "httpx.Request") -> None:
        try:
            body = json.loads(request.content) if request.content else None
        except Exception:
            body = "<non-json>"
        wire.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "direction": "request", "method": request.method,
            "url": str(request.url), "headers": _redact(request.headers),
            "body": body,
        }) + "\n")
        wire.flush()

    def on_response(response: "httpx.Response") -> None:
        response.read()
        try:
            body = json.loads(response.content)
        except Exception:
            body = "<non-json>"
        wire.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "direction": "response", "status": response.status_code,
            "body": body,
        }) + "\n")
        wire.flush()

    http_client = httpx.Client(
        event_hooks={"request": [on_request], "response": [on_response]}
    )
    return OpenAI(http_client=http_client, **base)


def run_live(question: str) -> str:
    client = _make_client()
    model = os.environ["LLM_MODEL"]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    for turn in range(MAX_TURNS):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=TOOL_SCHEMAS, temperature=0.2
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            trace("answer", f"turn {turn + 1}")
            return msg.content or ""
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            result = run_tool(call.function.name, json.loads(call.function.arguments or "{}"))
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )
    return "Stopped: reached max tool turns without a final answer."


def run_offline(question: str) -> str:
    """Scripted episode: same loop shape, deterministic plan, no model.

    Mirrors the source experiment's pipeline on the real dataset: alert feed
    first, Isolation Forest anomalies second, runbook lookup third.
    """
    trace("plan", f"offline episode for: {question!r}")
    alerts = json.loads(run_tool("get_active_alerts", {"component": "all"}))["alerts"]
    if not alerts:
        return "Alert feed is empty; nothing to investigate."
    critical = [a for a in alerts if a["severity"] == "CRITICAL"] or alerts
    lead = critical[0]
    component = lead["component"].lower()

    anomalies = json.loads(run_tool("detect_anomalies", {"nf": component}))["anomalies"]
    summary = json.loads(run_tool("get_kpi_summary", {"nf": component, "window_minutes": 60}))
    guidance = json.loads(run_tool("search_runbooks", {"query": lead["type"].replace("_", " ").lower()}))
    runbook = guidance["results"][0]["runbook"] if guidance["results"] else "none found"

    deltas = ", ".join(f"{k} {v:+.1f}%" for k, v in list(lead["kpi_delta_percent"].items())[:4]) or "see alert feed"
    return (
        f"Findings: {len(alerts)} alerts in feed; lead incident {lead['type']} "
        f"({lead['severity']}) on {lead['component']} from {lead['start_time']} "
        f"to {lead['end_time']}; KPI movement: {deltas}. Isolation Forest "
        f"flags {len(anomalies)} anomalous readings on {lead['component']}.\n"
        f"Probable cause: {lead['description']}.\n"
        f"Recommended next action: follow {runbook}; validate KPIs against the "
        f"last-hour summary after remediation."
    )


def _enable_mlflow() -> None:
    """Optional: mirror agent traces to MLflow (RHOAI 3.x Experiments tab).

    Plain MLflow needs only MLFLOW_TRACKING_URI. RHOAI's managed MLflow is
    workspace-scoped and RBAC-fronted: every request must carry an
    X-MLFLOW-WORKSPACE header (the data science project name) and a Bearer
    token authorized on that namespace. Set MLFLOW_WORKSPACE to enable the
    header shim; the token comes from MLFLOW_TRACKING_TOKEN or, in-cluster,
    from the pod's ServiceAccount. Tracing must never break the agent loop.
    """
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        return
    workspace = os.environ.get("MLFLOW_WORKSPACE")
    try:
        if workspace:
            if not os.environ.get("MLFLOW_TRACKING_TOKEN"):
                sa_token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
                if sa_token.exists():
                    os.environ["MLFLOW_TRACKING_TOKEN"] = sa_token.read_text().strip()
            # RHOAI's MLflow serves the cluster-internal service cert
            os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")

            from mlflow.utils import rest_utils

            _orig_http_request = rest_utils.http_request

            def _with_workspace(host_creds, endpoint, method, *a, **kw):
                headers = dict(kw.pop("extra_headers", None) or {})
                headers["X-MLFLOW-WORKSPACE"] = workspace
                return _orig_http_request(host_creds, endpoint, method, *a,
                                          extra_headers=headers, **kw)

            rest_utils.http_request = _with_workspace

        import mlflow

        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT", "101-noc-assistant"))
        mlflow.openai.autolog()
        suffix = f" (workspace {workspace})" if workspace else ""
        trace("mlflow", f"autolog enabled -> {uri}{suffix}")
    except Exception as exc:  # tracing must never break the loop
        trace("mlflow", f"disabled ({exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description="NOC Assistant agent")
    parser.add_argument("question", nargs="?", default="Why is UPF throughput degraded?")
    parser.add_argument("--offline", action="store_true", help="run scripted episode without an LLM endpoint")
    args = parser.parse_args()

    _enable_mlflow()

    answer = run_offline(args.question) if args.offline else run_live(args.question)
    print("\n=== NOC Assistant ===")
    print(answer)


if __name__ == "__main__":
    main()
