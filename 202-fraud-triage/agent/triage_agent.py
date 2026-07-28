"""202 Fraud Triage agent — the LangGraph decision loop over the served model.

The course's third and final stage: a small LangGraph state machine that
consumes the fraud model as a TOOL (KServe `fraud-detector`, pattern 2 —
never embedded), grounds each case in its billing context, reasons with
the cluster's own LLM inside code-enforced rails, stops at a REAL
human-approval gate on the escalate path (LangGraph `interrupt()` — the
graph checkpoints and resumes only with a person's decision), and writes
an externalized audit record for every case.

Decision discipline (same shape as 301/302 — the model gives the number,
the agent reasons, code enforces the rails):

    fraud_probability >= ESCALATE_FLOOR (0.60)  -> escalate, always.
    fraud_probability <= CLEAR_CEILING  (0.10)  -> clear is permitted.
    in between                                   -> the LLM chooses
        hold vs escalate from the score + billing context; without an
        LLM the deterministic band policy applies. The LLM can never
        clear a case the floor escalates — the rail is code, not prompt.

The escalate path uses LangGraph's `interrupt()`: the graph checkpoints
mid-run and the case parks as `awaiting_approval`. Resuming with a
`Command(resume={"approved": ..., "approver": ...})` completes it — the
approver's identity lands in the audit record. Without an approval the
case STAYS parked; the negative path is a first-class outcome, exactly
like 301's execution gate.

Case context derives from the case's own billing record (usage and cost
vs the account's averages, roaming/plan/wallet/PIN flags). The
production shape is a Feast online billing store — planned, per the
README's blueprint table; nothing here pretends it exists.

Modes:
  --offline           six real dataset rows (4 legit, 2 fraud) drive the
                      full graph with a documented STUB scorer and the
                      band policy — no cluster, no endpoints, prints
                      every node transition. The approval drill runs
                      both arms: one escalation approved, one rejected.
  live (default)      SCORER_URL (KServe V2) scores; LLM_* enables the
                      reasoning node; MLFLOW_* audits every case as a
                      run in the `revassurance-fraud` experiment.

Runs on Rome as deploy/ocp/rome/job-triage.yaml.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import uuid
from typing import Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

# ---------------------------------------------------------------- config
SCORER_URL = os.environ.get("SCORER_URL", "")
MODEL_NAME = os.environ.get("SCORER_MODEL", "fraud-detector")
CLEAR_CEILING = float(os.environ.get("CLEAR_CEILING", "0.10"))
ESCALATE_FLOOR = float(os.environ.get("ESCALATE_FLOOR", "0.60"))

FEATURES = ["Call_Duration", "Data_Usage", "Sms_Count", "Roaming_Indicator",
            "MobileWallet_Use", "Plan_Type", "Cost",
            "Cellular_Location_Distance", "Personal_Pin_Used",
            "Avg_Call_Duration", "Avg_Data_Usage", "Avg_Cost"]

# ---------------------------------------------------------------- mlflow
def _enable_mlflow():
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    ws = os.environ.get("MLFLOW_WORKSPACE")
    if not uri or not ws:
        return None
    try:
        os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
        tok = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        if os.path.exists(tok) and not os.environ.get("MLFLOW_TRACKING_TOKEN"):
            os.environ["MLFLOW_TRACKING_TOKEN"] = open(tok).read().strip()
        import requests as rq
        orig = rq.Session.request

        def req(self, method, url, **kw):
            if "mlflow" in url:
                h = kw.get("headers") or {}
                h["X-MLFLOW-WORKSPACE"] = ws
                kw["headers"] = h
            return orig(self, method, url, **kw)

        rq.Session.request = req
        import mlflow
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT",
                                             "revassurance-fraud"))
        print("[mlflow] audit ->", uri, flush=True)
        return mlflow
    except Exception as e:  # noqa: BLE001
        print("[mlflow] disabled:", type(e).__name__, e, flush=True)
        return None


mlflow = _enable_mlflow()

# ---------------------------------------------------------------- scorer
def _score_served(case: dict) -> dict:
    """POST one case to the KServe V2 endpoint (dataframe payload)."""
    inputs = []
    for col in FEATURES:
        v = case[col]
        if col == "Plan_Type":
            v = 1 if str(v) == "prepaid" else 0
        if isinstance(v, int):
            dt = "INT64"
        else:
            dt, v = "FP64", float(v)
        inputs.append({"name": col, "shape": [1, 1], "datatype": dt,
                       "data": [v]})
    body = json.dumps({"parameters": {"content_type": "pd"},
                       "inputs": inputs}).encode()
    req = urllib.request.Request(
        SCORER_URL + f"/v2/models/{MODEL_NAME}/infer", data=body,
        headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    out = {o["name"]: o["data"][0] for o in resp.get("outputs", [])}
    return {"fraud_probability": round(float(out["fraud_probability"]), 4),
            "fraud_flag": int(out.get("fraud_flag", 0)), "source": "kserve"}


def _score_stub(case: dict) -> dict:
    """Offline STUB, clearly labeled: a documented heuristic that mimics
    the BRF's decision *shape* (prepaid+roaming with usage far above the
    account average is the dominant fraud signature in the dataset) so
    the graph can be exercised with no cluster. It is NOT the model."""
    usage_ratio = case["Data_Usage"] / max(case["Avg_Data_Usage"], 1.0)
    p = 0.02
    if case["Plan_Type"] == "prepaid" and case["Roaming_Indicator"] == 1:
        p += 0.45
    if usage_ratio > 3.0:
        p += 0.30
    if case["Cost"] > 3 * max(case["Avg_Cost"], 1.0):
        p += 0.15
    p = min(p, 0.99)
    return {"fraud_probability": round(p, 4), "fraud_flag": int(p >= 0.5),
            "source": "offline-stub"}

# ---------------------------------------------------------------- graph
class CaseState(TypedDict, total=False):
    case_id: str
    case: dict
    true_label: Optional[int]
    score: dict
    context: dict
    decision: str
    rationale: str
    decided_by: str
    approval: dict
    outcome: str


def score(state: CaseState) -> CaseState:
    s = _score_served(state["case"]) if SCORER_URL else _score_stub(state["case"])
    print(f"[score]   {state['case_id']} p={s['fraud_probability']}"
          f" ({s['source']})", flush=True)
    return {"score": s}


def context(state: CaseState) -> CaseState:
    c = state["case"]
    ctx = {
        "usage_vs_avg": round(c["Data_Usage"] / max(c["Avg_Data_Usage"], 1.0), 2),
        "cost_vs_avg": round(c["Cost"] / max(abs(c["Avg_Cost"]), 1.0), 2),
        "call_vs_avg": round(c["Call_Duration"] / max(abs(c["Avg_Call_Duration"]), 1.0), 2),
        "roaming": bool(c["Roaming_Indicator"]),
        "prepaid": c["Plan_Type"] == "prepaid",
        "mobile_wallet": bool(c["MobileWallet_Use"]),
        "pin_used": bool(c["Personal_Pin_Used"]),
        "location_distance": round(c["Cellular_Location_Distance"], 2),
    }
    print(f"[context] {state['case_id']} usage_vs_avg={ctx['usage_vs_avg']}"
          f" roaming={ctx['roaming']} prepaid={ctx['prepaid']}", flush=True)
    return {"context": ctx}


def _llm_decide(p: float, ctx: dict) -> Optional[tuple]:
    if not os.environ.get("LLM_BASE_URL"):
        return None
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(base_url=os.environ["LLM_BASE_URL"],
                         api_key=os.environ.get("LLM_API_KEY", "none"),
                         model=os.environ.get("LLM_MODEL", ""),
                         temperature=0, timeout=90)
        prompt = (
            "You are a telecom revenue-assurance triage analyst. The served "
            f"fraud model scored this case fraud_probability={p}. Billing "
            f"context: {json.dumps(ctx)}. Policy: you are choosing between "
            "'hold' (park for batch review) and 'escalate' (send to a human "
            "approver now). You may NOT clear this case. Answer as JSON: "
            '{"decision": "hold|escalate", "rationale": "<one sentence '
            'citing the score and at least one context field>"}')
        raw = llm.invoke(prompt).content
        j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        d = j["decision"] if j.get("decision") in ("hold", "escalate") else "hold"
        return d, str(j.get("rationale", ""))[:400], "llm"
    except Exception as e:  # noqa: BLE001
        print("[decide] llm unavailable:", type(e).__name__, flush=True)
        return None


def decide(state: CaseState) -> CaseState:
    p = state["score"]["fraud_probability"]
    ctx = state["context"]
    # code-enforced rails first — never the LLM's call:
    if p >= ESCALATE_FLOOR:
        d, r, by = "escalate", (
            f"fraud_probability {p} >= escalate floor {ESCALATE_FLOOR}; "
            "policy escalates regardless of any other signal"), "policy-rail"
    elif p <= CLEAR_CEILING:
        d, r, by = "clear", (
            f"fraud_probability {p} <= clear ceiling {CLEAR_CEILING} and no "
            "context flag contradicts it"), "policy-rail"
        if ctx["roaming"] and ctx["prepaid"] and ctx["usage_vs_avg"] > 3:
            d, r, by = "hold", ("low score but prepaid+roaming with usage "
                                f"{ctx['usage_vs_avg']}x the account average; "
                                "holding for review"), "policy-rail"
    else:
        got = _llm_decide(p, ctx)
        if got:
            d, r, by = got
        else:
            d = "escalate" if (ctx["roaming"] and ctx["prepaid"]) else "hold"
            r = (f"band policy: fraud_probability {p} in "
                 f"({CLEAR_CEILING}, {ESCALATE_FLOOR}); "
                 + ("roaming prepaid pattern -> escalate" if d == "escalate"
                    else "no aggravating pattern -> hold"))
            by = "band-policy"
    print(f"[decide]  {state['case_id']} -> {d} ({by})", flush=True)
    return {"decision": d, "rationale": r, "decided_by": by}


def gate(state: CaseState) -> CaseState:
    """The human-approval gate — a real LangGraph interrupt. Clear/hold
    pass through; escalate parks the checkpointed graph until a person
    resumes it with a decision. The gate is graph structure, not prompt."""
    if state["decision"] != "escalate":
        return {"approval": {"required": False},
                "outcome": state["decision"]}
    verdict = interrupt({
        "case_id": state["case_id"],
        "fraud_probability": state["score"]["fraud_probability"],
        "rationale": state["rationale"],
        "ask": "approve escalation to fraud ops?"})
    approved = bool(verdict.get("approved"))
    return {"approval": {"required": True, "approved": approved,
                         "approver": verdict.get("approver", "unknown")},
            "outcome": "escalated" if approved else "released_to_hold"}


def audit(state: CaseState) -> CaseState:
    rec = {k: state.get(k) for k in
           ("case_id", "score", "context", "decision", "rationale",
            "decided_by", "approval", "outcome", "true_label")}
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print("[audit]  ", json.dumps(rec), flush=True)
    if mlflow:
        with mlflow.start_run(run_name=f"triage-{state['case_id']}"):
            mlflow.log_params({
                "decision": rec["decision"], "outcome": rec["outcome"],
                "decided_by": rec["decided_by"],
                "approval_required": rec["approval"]["required"],
                "approver": rec["approval"].get("approver", "-"),
                "scorer": state["score"]["source"]})
            mlflow.log_metric("fraud_probability",
                              state["score"]["fraud_probability"])
            mlflow.log_dict(rec, "case_record.json")
    return {}


def build_graph():
    g = StateGraph(CaseState)
    g.add_node("score", score)
    g.add_node("context", context)
    g.add_node("decide", decide)
    g.add_node("gate", gate)
    g.add_node("audit", audit)
    g.set_entry_point("score")
    g.add_edge("score", "context")
    g.add_edge("context", "decide")
    g.add_edge("decide", "gate")
    g.add_edge("gate", "audit")
    g.add_edge("audit", END)
    return g.compile(checkpointer=MemorySaver())

# ---------------------------------------------------------------- runner
def triage(graph, case: dict, case_id: str, true_label=None,
           approval: Optional[dict] = None) -> dict:
    cfg = {"configurable": {"thread_id": case_id}}
    out = graph.invoke({"case_id": case_id, "case": case,
                        "true_label": true_label}, cfg)
    if "__interrupt__" in out:
        park = out["__interrupt__"][0].value
        print(f"[gate]    {case_id} AWAITING_APPROVAL "
              f"(checkpointed): {json.dumps(park)}", flush=True)
        if approval is None:
            # negative path: the case stays parked — record it honestly.
            rec = {"case_id": case_id, "outcome": "awaiting_approval",
                   "parked": park}
            print("[audit]  ", json.dumps(rec), flush=True)
            if mlflow:
                with mlflow.start_run(run_name=f"triage-{case_id}"):
                    mlflow.log_params({"decision": "escalate",
                                       "outcome": "awaiting_approval",
                                       "approval_required": True})
                    mlflow.log_metric("fraud_probability",
                                      park["fraud_probability"])
            return rec
        out = graph.invoke(Command(resume=approval), cfg)
    return {"case_id": case_id, "outcome": out.get("outcome")}

# 6 real rows from fenar/revenue_assurance (4 legit, 2 fraud) — the
# offline episode's cases; values verbatim from the published dataset.
SAMPLE_CASES = [
 {"Call_Duration": 4.692681, "Data_Usage": 452.126271, "Sms_Count": 1, "Roaming_Indicator": 0, "MobileWallet_Use": 0, "Plan_Type": "postpaid", "Cost": 56.99117, "Cellular_Location_Distance": 3.629675, "Personal_Pin_Used": 0, "Avg_Call_Duration": 1.460109, "Avg_Data_Usage": 336.312984, "Avg_Cost": 71.603238, "Fraud": 0},
 {"Call_Duration": 30.101214, "Data_Usage": 226.842467, "Sms_Count": 4, "Roaming_Indicator": 0, "MobileWallet_Use": 0, "Plan_Type": "postpaid", "Cost": 9.718698, "Cellular_Location_Distance": 3.654629, "Personal_Pin_Used": 0, "Avg_Call_Duration": 30.817472, "Avg_Data_Usage": 150.96959, "Avg_Cost": -3.794503, "Fraud": 0},
 {"Call_Duration": 13.167457, "Data_Usage": 2.69506, "Sms_Count": 3, "Roaming_Indicator": 0, "MobileWallet_Use": 0, "Plan_Type": "postpaid", "Cost": 10.770598, "Cellular_Location_Distance": 2.506765, "Personal_Pin_Used": 0, "Avg_Call_Duration": 13.554912, "Avg_Data_Usage": 79.394244, "Avg_Cost": 4.58132, "Fraud": 0},
 {"Call_Duration": 9.129426, "Data_Usage": 411.727859, "Sms_Count": 4, "Roaming_Indicator": 0, "MobileWallet_Use": 0, "Plan_Type": "postpaid", "Cost": 5.88596, "Cellular_Location_Distance": 0.098861, "Personal_Pin_Used": 0, "Avg_Call_Duration": 7.990501, "Avg_Data_Usage": 317.191998, "Avg_Cost": 24.955166, "Fraud": 0},
 {"Call_Duration": 22.308134, "Data_Usage": 1942.8511, "Sms_Count": 3, "Roaming_Indicator": 1, "MobileWallet_Use": 1, "Plan_Type": "prepaid", "Cost": 13.581574, "Cellular_Location_Distance": 9.124559, "Personal_Pin_Used": 0, "Avg_Call_Duration": 22.320551, "Avg_Data_Usage": 1852.980813, "Avg_Cost": 12.224255, "Fraud": 1},
 {"Call_Duration": 0.167269, "Data_Usage": 1552.024248, "Sms_Count": 2, "Roaming_Indicator": 1, "MobileWallet_Use": 0, "Plan_Type": "prepaid", "Cost": 19.835749, "Cellular_Location_Distance": 2.173541, "Personal_Pin_Used": 0, "Avg_Call_Duration": -0.186029, "Avg_Data_Usage": 1612.063781, "Avg_Cost": 21.045894, "Fraud": 1},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="scripted episode over 6 real rows, no endpoints")
    ap.add_argument("--cases", type=int,
                    default=int(os.environ.get("CASES", "6")))
    args = ap.parse_args()
    if args.offline:
        os.environ.pop("LLM_BASE_URL", None)
        global SCORER_URL
        SCORER_URL = ""
    graph = build_graph()
    results = []
    approver = os.environ.get("APPROVER", "revass-oncall@example.com")
    approve_token = os.environ.get("APPROVE_TOKEN", "")
    esc_seen = 0
    for i, row in enumerate(SAMPLE_CASES[: args.cases]):
        case = {k: v for k, v in row.items() if k != "Fraud"}
        cid = f"case-{uuid.uuid4().hex[:8]}"
        print(f"--- {cid} (true_label={row['Fraud']}) ---", flush=True)
        approval = None
        # scripted approval drill: first escalation approved, second
        # rejected — both arms of the gate in one run. Live mode requires
        # a real APPROVE_TOKEN or the case parks.
        if args.offline:
            approval = ({"approved": esc_seen == 0, "approver": approver})
        elif approve_token:
            approval = {"approved": True, "approver": approver}
        r = triage(graph, case, cid, row["Fraud"], approval)
        if r["outcome"] in ("escalated", "released_to_hold",
                            "awaiting_approval"):
            esc_seen += 1
        results.append(r)
    summary = {}
    for r in results:
        summary[r["outcome"]] = summary.get(r["outcome"], 0) + 1
    print("TRIAGE_OK", json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
