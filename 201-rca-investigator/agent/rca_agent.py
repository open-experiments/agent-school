"""RCA Investigator: a two-phase research agent with Tier-2 model routing.

Phase 1 (investigate): a small model drives the tool loop, pulling the
incident and retrieving evidence from the RAG backend.
Phase 2 (write): a large model gets the collected evidence and writes the
RCA report; every claim must cite retrieved record ids.

Tier-2 routing in miniature: cheap model where volume is, strong model where
judgment is. Set LLM_MODEL_SMALL / LLM_MODEL_LARGE (both fall back to
LLM_MODEL). The report is written to reports/ so the session stays ephemeral.

Offline mode (--offline) runs the same shape deterministically with no LLM.
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

BASE_DIR = Path(__file__).resolve().parent.parent
# Containers run with a random UID on OpenShift; point REPORT_DIR at a
# writable mount (emptyDir/PVC) there. Defaults to the repo dir locally.
REPORT_DIR = Path(os.environ.get("REPORT_DIR", BASE_DIR / "reports"))

INVESTIGATOR_PROMPT = (
    "You are an RCA investigator for a 5G core. Gather evidence: first call "
    "get_incident, then call retrieve_evidence with 2-4 focused queries "
    "(KPI names, alert types, time windows). When you have enough evidence, "
    "reply with a short bullet list of the key evidence found. Do not write "
    "the report."
)

WRITER_PROMPT = (
    "You are writing a root-cause analysis for a 5G core incident. Use ONLY "
    "the evidence records provided. Every factual claim must cite record ids "
    "in brackets, like [amf-281] or [alert-0]. Structure: Incident, Evidence, "
    "Root cause, Contributing factors, Recommended remediation. If evidence "
    "is insufficient for a claim, say so rather than inventing it."
)

# Investigation budget: max phase-1 tool turns. A real operational knob
# (cost and latency bound); the loop hands whatever evidence it gathered to
# phase 2 when the budget runs out.
MAX_TURNS = int(os.environ.get("RCA_MAX_TURNS", "6"))


def trace(step: str, detail: str) -> None:
    print(f"[trace] {time.strftime('%H:%M:%S')} {step:<12} {detail}", flush=True)


def run_tool(name: str, args: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"})
    trace("tool-call", f"{name}({json.dumps(args)})")
    result = handler(**args)
    trace("tool-result", result[:160] + ("..." if len(result) > 160 else ""))
    return result


def _make_client():
    """OpenAI client; LLM_WIRE_LOG appends wire-level JSONL (auth redacted)."""
    from openai import OpenAI

    base = dict(base_url=os.environ["LLM_BASE_URL"],
                api_key=os.environ.get("LLM_API_KEY", "none"))
    wire_path = os.environ.get("LLM_WIRE_LOG")
    if not wire_path:
        return OpenAI(**base)

    import httpx
    wire = open(wire_path, "a", encoding="utf-8")

    def dump(direction: str, **fields) -> None:
        wire.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "direction": direction, **fields}) + "\n")
        wire.flush()

    def on_request(request: "httpx.Request") -> None:
        try:
            body = json.loads(request.content) if request.content else None
        except Exception:
            body = "<non-json>"
        headers = {k: ("***REDACTED***" if k.lower() == "authorization" else v)
                   for k, v in request.headers.items()}
        dump("request", method=request.method, url=str(request.url),
             headers=headers, body=body)

    def on_response(response: "httpx.Response") -> None:
        response.read()
        try:
            body = json.loads(response.content)
        except Exception:
            body = "<non-json>"
        dump("response", status=response.status_code, body=body)

    http_client = httpx.Client(event_hooks={"request": [on_request],
                                            "response": [on_response]})
    return OpenAI(http_client=http_client, **base)


def save_report(text: str) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"rca_{time.strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(text, encoding="utf-8")
    trace("artifact", str(path))
    return path


def run_live(question: str) -> str:
    client = _make_client()
    small = os.environ.get("LLM_MODEL_SMALL") or os.environ["LLM_MODEL"]
    large = os.environ.get("LLM_MODEL_LARGE") or os.environ["LLM_MODEL"]

    # Phase 1: investigation loop on the small model
    trace("phase-1", f"investigate with {small}")
    messages = [{"role": "system", "content": INVESTIGATOR_PROMPT},
                {"role": "user", "content": question}]
    evidence_blobs: list[str] = []
    for _ in range(MAX_TURNS):
        resp = client.chat.completions.create(
            model=small, messages=messages, tools=TOOL_SCHEMAS, temperature=0.2)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            break
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            result = run_tool(call.function.name,
                              json.loads(call.function.arguments or "{}"))
            evidence_blobs.append(result)
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": result})

    # Phase 2: report writing on the large model, evidence-only context.
    # RCA_DISABLE_THINKING=1 (default) disables hybrid-reasoning token burn
    # for the write-up via vLLM's chat_template_kwargs (verified against
    # Qwen3 on RHOAI MaaS); thinking stays ON for phase-1 tool planning.
    trace("phase-2", f"write report with {large}")
    evidence = "\n\n".join(evidence_blobs)[:24000]
    extra = ({"chat_template_kwargs": {"enable_thinking": False}}
             if os.environ.get("RCA_DISABLE_THINKING", "1") == "1" else None)
    resp = client.chat.completions.create(
        model=large, temperature=0.2,
        max_tokens=int(os.environ.get("RCA_REPORT_MAX_TOKENS", "1200")),
        extra_body=extra,
        messages=[{"role": "system", "content": WRITER_PROMPT},
                  {"role": "user",
                   "content": f"Incident question: {question}\n\nEvidence records:\n{evidence}"}])
    report = resp.choices[0].message.content or ""
    save_report(report)
    return report


def run_offline(question: str) -> str:
    """Deterministic episode: same two-phase shape, no LLM."""
    trace("phase-1", "investigate (offline)")
    incident = json.loads(run_tool("get_incident", {"component": "all"}))
    lead = ([a for a in incident["alerts"] if a["severity"] == "CRITICAL"]
            or incident["alerts"])[0]
    comp = lead["component"].lower()
    q1 = json.loads(run_tool("retrieve_evidence",
                             {"query": lead["type"].replace("_", " ").lower(),
                              "nf": comp, "k": 3}))
    q2 = json.loads(run_tool("retrieve_evidence",
                             {"query": f"{comp} {lead['start_time'][:16]}",
                              "nf": comp, "k": 3}))
    trace("phase-2", "write report (offline template)")
    cites = [r["id"] for r in q1["results"] + q2["results"]][:6]
    deltas = ", ".join(f"{k} {v:+.1f}%" for k, v in lead["kpi_delta_percent"].items())
    report = (
        f"# RCA: {lead['type']} on {lead['component']}\n\n"
        f"## Incident\n{lead['description']} from {lead['start_time']} to "
        f"{lead['end_time']} [alert feed].\n\n"
        f"## Evidence\nKPI movement during window: {deltas or 'see alert feed'} "
        f"[{cites[0] if cites else 'n/a'}]. Correlated records: "
        f"{', '.join('[' + c + ']' for c in cites)}.\n\n"
        f"## Root cause\n{lead['type'].replace('_', ' ').title()} on "
        f"{lead['component']} per alert feed and correlated telemetry.\n\n"
        f"## Recommended remediation\nFollow the matching runbook in course "
        f"101; validate KPIs post-change.\n"
    )
    save_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="RCA Investigator agent")
    parser.add_argument("question", nargs="?",
                        default="Produce an RCA for the most severe current incident in the 5G core.")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    answer = run_offline(args.question) if args.offline else run_live(args.question)
    print("\n=== RCA Investigator ===")
    print(answer)


if __name__ == "__main__":
    main()
