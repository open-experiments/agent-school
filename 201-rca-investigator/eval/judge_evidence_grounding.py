"""LLM-as-a-Judge: evidence-grounding for RCA traces, judged by the
platform's own served model (Kimi-Linear on vLLM) — no external API.

Mirrors the `evidence-grounding` judge defined in the RHOAI dashboard
(Experiments -> 201-rca-investigator -> Judges) and logs each verdict as
feedback on the trace, which the Experiments UI renders in the trace's
Assessments panel.

Why a runner script exists at all: in RHOAI 3.5 EA2 the dashboard's
"Run judges" action executes inside the MLflow server, where (a) the jobs
status API 404s under workspace scoping, (b) the `openai:/` judge adapter
ignores OPENAI_API_BASE and hardcodes api.openai.com, and (c) the operator
NetworkPolicy blocks egress to model endpoints on non-standard ports. This
script runs the identical judge client-side against the same tracking
server, sidestepping all three (details: deploy/README.md, "LLM-as-a-Judge").

Env (see deploy/ocp/rome): MLFLOW_TRACKING_URI, MLFLOW_WORKSPACE,
HOSTED_VLLM_API_BASE (the vLLM /v1 endpoint), HOSTED_VLLM_API_KEY.

Usage:
    python eval/judge_evidence_grounding.py tr-<id> [tr-<id> ...]
    python eval/judge_evidence_grounding.py --latest 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

WORKSPACE = os.environ.get("MLFLOW_WORKSPACE")

JUDGE_MODEL = os.environ.get(
    "JUDGE_MODEL", "hosted_vllm:/kimi-linear-48b-a3b"
)

INSTRUCTIONS = (
    "You are evaluating a root-cause-analysis agent for a 5G core network "
    "(AMF/SMF/UPF). The agent request (question plus retrieved evidence "
    "records) is in {{ inputs }} and the agent's response is in "
    "{{ outputs }}. Judge whether the response is GROUNDED IN EVIDENCE: "
    "every material factual claim (alert types, components, severities, "
    "timestamps, KPI deltas) must be supported by the evidence records in "
    "the inputs, and evidence-derived claims should cite record ids in "
    "brackets such as [alert-0]. Answer 'yes' only if all material claims "
    "are supported with no invented facts; 'no' otherwise. Give a "
    "one-paragraph rationale naming any unsupported or inconsistent claims."
)


def _install_workspace_shim() -> None:
    """RHOAI's MLflow is workspace-scoped; same shim as agent/_enable_mlflow."""
    if not WORKSPACE:
        return
    if not os.environ.get("MLFLOW_TRACKING_TOKEN"):
        sa = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        if sa.exists():
            os.environ["MLFLOW_TRACKING_TOKEN"] = sa.read_text().strip()
    os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")

    from mlflow.utils import rest_utils

    _orig = rest_utils.http_request

    def _shim(host_creds, endpoint, method, *a, **kw):
        headers = dict(kw.pop("extra_headers", None) or {})
        headers["X-MLFLOW-WORKSPACE"] = WORKSPACE
        if endpoint == "/v1/traces" and host_creds.host.rstrip("/").endswith("/mlflow"):
            import copy

            host_creds = copy.copy(host_creds)
            host_creds.host = host_creds.host.rstrip("/")[: -len("/mlflow")]
        return _orig(host_creds, endpoint, method, *a, extra_headers=headers, **kw)

    rest_utils.http_request = _shim
    from mlflow.store.tracking import rest_store

    rest_store.http_request = _shim


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_ids", nargs="*", help="trace ids to judge")
    parser.add_argument("--latest", type=int, default=0,
                        help="judge the N newest traces of the experiment")
    parser.add_argument("--experiment", default=os.environ.get(
        "MLFLOW_EXPERIMENT", "201-rca-investigator"))
    args = parser.parse_args()

    _install_workspace_shim()
    import mlflow
    from mlflow.genai.judges import make_judge

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    client = mlflow.MlflowClient()

    trace_ids = list(args.trace_ids)
    if args.latest:
        exp = client.get_experiment_by_name(args.experiment)
        traces = client.search_traces(
            locations=[exp.experiment_id],
            max_results=args.latest, order_by=["timestamp_ms DESC"])
        trace_ids += [t.info.trace_id for t in traces]
    if not trace_ids:
        sys.exit("no traces given; pass trace ids or --latest N")

    judge = make_judge(name="evidence-grounding",
                       instructions=INSTRUCTIONS, model=JUDGE_MODEL)

    for tid in trace_ids:
        trace = client.get_trace(tid)
        spans = trace.data.spans
        root = next((s for s in spans if not s.parent_id), spans[0])
        inputs = json.dumps(root.inputs, default=str)[:9000]
        outputs = json.dumps(root.outputs, default=str)[:9000]
        feedback = judge(inputs={"request": inputs}, outputs=outputs)
        print(f"{tid} -> {feedback.value}\n  {feedback.rationale}\n")
        mlflow.log_feedback(
            trace_id=tid, name="evidence-grounding", value=feedback.value,
            rationale=feedback.rationale,
            source=mlflow.entities.AssessmentSource(
                source_type="LLM_JUDGE", source_id=JUDGE_MODEL))
    print(f"logged feedback for {len(trace_ids)} traces")


if __name__ == "__main__":
    main()
