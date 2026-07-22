"""302 Energy Optimizer — the Llama Stack loop, simulate-before-act.

One optimization episode per run:

  propose  — a Llama Stack agent session (Agents API, remote-vLLM →
             the cluster's Kimi endpoint) proposes cell-sleep windows
             over the known diurnal traffic shape. Session memory
             carries prior rejections into revision turns.
  dispatch — pattern 3: the proposal becomes a Kueue-admitted
             simulation Job, created and polled through the K8s API
             under the submit/poll-only Role (sim-rbac.yaml). The
             compute never runs in this pod.
  score    — pattern 2: the simulated network condition is scored by
             the served sustainability model (KServe V2). Bridge,
             documented: a dataset-median KPI row with the simulated
             QoS impact applied (packet loss / call drops raised by
             the sim's dropped-traffic percentage).
  gate     — in code, not in the prompt: accept only if
             savings_pct >= MIN_SAVINGS_PCT and qos_dropped_pct <=
             MAX_QOS_DROP_PCT and energy_efficiency >= MIN_EFFICIENCY.
             Rejected proposals are logged to MLflow and fed back to
             the agent for revision (up to MAX_ROUNDS).
  emit     — the agent's ONLY output is a change-plan artifact with
             the simulation evidence and score attached
             (change_plan.json in the MLflow run). It never touches a
             network.

Runs as deploy/ocp/rome/job-optimize.yaml (SA energy-optimizer).
"""
import json
import os
import ssl
import time
import urllib.request
import uuid
from pathlib import Path

MIN_SAVINGS_PCT = float(os.environ.get("MIN_SAVINGS_PCT", "3.0"))
MAX_QOS_DROP_PCT = float(os.environ.get("MAX_QOS_DROP_PCT", "0.5"))
MIN_EFFICIENCY = float(os.environ.get("MIN_EFFICIENCY", "60.0"))
MAX_ROUNDS = int(os.environ.get("MAX_ROUNDS", "3"))
CELLS = int(os.environ.get("CELLS", "6"))

LLAMA_STACK_URL = os.environ.get(
    "LLAMA_STACK_URL",
    "http://llama-stack.agent-school.svc.cluster.local:8321")
SCORER_URL = os.environ.get(
    "SCORER_URL",
    "http://sustainability-scorer-predictor.agent-school.svc.cluster.local:8080")
NAMESPACE = "agent-school"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"

# Dataset-median KPI row for the scorer bridge (medians of the
# published 100K-row 5G netops dataset; the sim's QoS impact is
# applied on top — documented modeling bridge, not measurement).
BASELINE_KPIS = {
    "Cell Availability (%)": 98.6, "MTTR (hours)": 3.4,
    "Throughput (Mbps)": 500.0, "Latency (ms)": 50.0,
    "Packet Loss Rate (%)": 2.0, "Call Drop Rate (%)": 1.0,
    "Handover Success Rate (%)": 97.0, "Alarm Count": 12,
    "Critical Alarm Count": 3, "Temperature (°C)": 15.0,
    "Humidity (%)": 55.0,
}


# ---------------------------------------------------------------- mlflow
def _enable_mlflow():
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        return None
    ws = os.environ.get("MLFLOW_WORKSPACE")
    try:
        if ws:
            if not os.environ.get("MLFLOW_TRACKING_TOKEN"):
                sa = Path(SA_DIR + "/token")
                if sa.exists():
                    os.environ["MLFLOW_TRACKING_TOKEN"] = sa.read_text().strip()
            os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
            from mlflow.utils import rest_utils
            orig = rest_utils.http_request

            def shim(host_creds, endpoint, method, *a, **kw):
                headers = dict(kw.pop("extra_headers", None) or {})
                headers["X-MLFLOW-WORKSPACE"] = ws
                return orig(host_creds, endpoint, method, *a,
                            extra_headers=headers, **kw)

            rest_utils.http_request = shim
            from mlflow.store.tracking import rest_store
            rest_store.http_request = shim
            import requests as rq
            orig_req = rq.Session.request

            def req(self, method, url, **kw):
                if "mlflow" in url:
                    h = kw.get("headers") or {}
                    h["X-MLFLOW-WORKSPACE"] = ws
                    kw["headers"] = h
                return orig_req(self, method, url, **kw)

            rq.Session.request = req
        import mlflow
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT",
                                             "302-energy-optimizer"))
        print("[mlflow] enabled", flush=True)
        return mlflow
    except Exception as e:
        print("[mlflow] disabled:", type(e).__name__, e, flush=True)
        return None


mlflow = _enable_mlflow()

# -------------------------------------------------------------- pattern 3
from kubernetes import client as k8s_client  # noqa: E402
from kubernetes import config as k8s_config  # noqa: E402

k8s_config.load_incluster_config()
BATCH = k8s_client.BatchV1Api()
CORE = k8s_client.CoreV1Api()


def dispatch_simulation(proposal, proposal_id):
    """Create one Kueue-admitted sim Job, poll it, parse SIM_RESULT."""
    name = "sim-" + proposal_id
    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": NAMESPACE,
                     "labels": {"kueue.x-k8s.io/queue-name":
                                "agent-school-queue",
                                "app": "cell-sleep-sim",
                                "app.kubernetes.io/part-of":
                                "agent-school"}},
        "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 7200,
                 "activeDeadlineSeconds": 1200,
                 "template": {"metadata": {"labels":
                                           {"app": "cell-sleep-sim"}},
                              "spec": {
             "restartPolicy": "Never",
             "volumes": [{"name": "src",
                          "configMap": {"name": "sim-src"}}],
             "containers": [{
                 "name": "sim",
                 "image": "registry.access.redhat.com/ubi9/"
                          "python-311:latest",
                 "envFrom": [{"configMapRef":
                              {"name": "mlflow-tracking"}}],
                 "env": [
                     {"name": "HOME", "value": "/tmp"},
                     {"name": "PIP_NO_CACHE_DIR", "value": "1"},
                     {"name": "MLFLOW_EXPERIMENT",
                      "value": "302-energy-optimizer"},
                     {"name": "PROPOSAL_ID", "value": proposal_id},
                     {"name": "PROPOSAL",
                      "value": json.dumps(proposal)}],
                 "command": ["/bin/sh", "-c",
                             'pip install -q "jax[cpu]" mlflow==3.4.0'
                             " && python3 /src/cell_sleep_sim.py"],
                 "volumeMounts": [{"name": "src",
                                   "mountPath": "/src"}],
                 "resources": {"requests": {"cpu": "1",
                                            "memory": "1Gi"},
                               "limits": {"cpu": "2",
                                          "memory": "2Gi"}},
                 "securityContext": {
                     "allowPrivilegeEscalation": False,
                     "capabilities": {"drop": ["ALL"]},
                     "runAsNonRoot": True,
                     "seccompProfile": {"type": "RuntimeDefault"}}}]}}}}
    BATCH.create_namespaced_job(NAMESPACE, job)
    print("[dispatch] job", name, "submitted through Kueue", flush=True)
    deadline = time.time() + 1200
    while time.time() < deadline:
        st = BATCH.read_namespaced_job_status(name, NAMESPACE).status
        if st.succeeded:
            break
        if st.failed:
            raise RuntimeError("sim job failed")
        time.sleep(10)
    # The sim mirrors its full result to MLflow (run sim-<proposal_id>
    # in 302-energy-optimizer): read it back from there. This is the
    # deterministic channel — parsing the sim pod's stdout was
    # unreliable (the jax install produces a huge log and the
    # completed-pod log read intermittently dropped the tail on Rome).
    return fetch_sim_from_mlflow(proposal_id)


def fetch_sim_from_mlflow(proposal_id):
    """Reconstruct the sim result dict from the sim's MLflow run
    metrics (logged by cell_sleep_sim.py's maybe_mlflow)."""
    if not mlflow:
        raise RuntimeError("mlflow unavailable; cannot read sim result")
    keys = ["baseline_kwh", "optimized_kwh", "savings_pct",
            "qos_dropped_pct", "cost_savings_usd", "co2_savings_kg"]
    for attempt in range(10):
        runs = mlflow.search_runs(
            experiment_names=["302-energy-optimizer"],
            filter_string="attributes.run_name = 'sim-" + proposal_id + "'",
            max_results=1)
        if len(runs):
            row = runs.iloc[0]
            out = {}
            for k in keys:
                col = "metrics." + k
                if col in row and row[col] == row[col]:  # not NaN
                    out[k] = float(row[col])
            if all(k in out for k in ("savings_pct", "qos_dropped_pct")):
                out["savings_kwh"] = round(
                    out.get("baseline_kwh", 0) - out.get("optimized_kwh", 0), 2)
                return out
        time.sleep(6)
    raise RuntimeError("sim MLflow run sim-" + proposal_id + " not found")


# -------------------------------------------------------------- pattern 2
def score_simulation(sim):
    """Score the simulated network condition on the served model."""
    kpis = dict(BASELINE_KPIS)
    drop = float(sim["qos_dropped_pct"])
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


# ---------------------------------------------------------------- propose
from llama_stack_client import LlamaStackClient  # noqa: E402
from llama_stack_client.lib.agents.agent import Agent  # noqa: E402

LS = LlamaStackClient(base_url=LLAMA_STACK_URL)
MODEL = os.environ.get("LS_MODEL", "kimi-linear-48b-a3b")

INSTRUCTIONS = """You are the proposal step of a RAN energy optimizer
for a cluster of {cells} cells (cell ids 0..{maxcell}), each with
1000 Mbps capacity. Traffic follows a diurnal shape: night trough
(~00:00-06:00, 20-35% load), morning ramp peaking ~09:30, evening
peak ~20:00 (highest). You propose cell-sleep windows; a separate
simulation decides their real effect, and a threshold gate accepts or
rejects — you only propose.

CRITICAL rule: put at most {half} of the {cells} cells to sleep — never
more. If every cell sleeps there is nowhere to re-home traffic and QoS
collapses; the gate will reject you. Prefer deep-night hours
(00:00-06:00) where load is lowest.

A GOOD proposal looks like this (2 cells, night only):
{{"windows": [{{"cell": 4, "start_hour": 0, "end_hour": 6}},
{{"cell": 5, "start_hour": 0, "end_hour": 6}}],
"rationale": "sleep two low-load cells overnight; the other four
absorb the light night traffic"}}

Respond with STRICT JSON only, no prose, in exactly that shape.
""".format(cells=CELLS, maxcell=CELLS - 1, half=CELLS // 2)


def parse_windows(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`\n")
        if t.startswith("json"):
            t = t[4:]
    start = t.find("{")
    if start > 0:
        t = t[start:]
    return json.loads(t)


def run_episode():
    episode = uuid.uuid4().hex[:10]
    agent = Agent(LS, model=MODEL, instructions=INSTRUCTIONS,
                  enable_session_persistence=True)
    session_id = agent.create_session("episode-" + episode)
    print("[episode]", episode, "session", session_id, flush=True)

    prompt = ("Propose sleep windows for tonight. Savings target: at "
              "least %.1f%% energy saved with at most %.2f%% dropped "
              "traffic." % (MIN_SAVINGS_PCT, MAX_QOS_DROP_PCT))
    attempts = []
    for round_no in range(1, MAX_ROUNDS + 2):  # +1 headroom for revises
        turn = agent.create_turn(
            messages=[{"role": "user", "content": prompt}],
            session_id=session_id, stream=False)
        text = turn.output_message.content
        proposal = parse_windows(text)
        proposal["cells"] = CELLS
        pid = episode + "-r" + str(round_no)
        wins = proposal.get("windows", [])
        print("[propose] round", round_no, json.dumps(wins)[:200],
              flush=True)

        # cheap pre-gate: don't burn a sim on a proposal that sleeps
        # more than half the cells (it can only fail QoS). Feed the
        # violation straight back as a rejection.
        sleeping_cells = {int(w["cell"]) for w in wins}
        if len(sleeping_cells) > CELLS // 2:
            print("[pre-gate] rejected: sleeps", len(sleeping_cells),
                  "cells >", CELLS // 2, flush=True)
            if mlflow:
                try:
                    with mlflow.start_run(run_name="reject-" + pid):
                        mlflow.log_param("episode", episode)
                        mlflow.log_param("round", round_no)
                        mlflow.log_param("accepted", False)
                        mlflow.log_param("pre_gate_reason",
                                         "sleeps more than half the cells")
                        mlflow.log_metric("sleeping_cells",
                                          len(sleeping_cells))
                except Exception as e:
                    print("[mlflow] log skipped:", e, flush=True)
            attempts.append({"round": round_no, "proposal": proposal,
                             "accepted": False,
                             "pre_gate": "sleeps more than half"})
            prompt = ("REJECTED round %d: you proposed sleeping %d cells "
                      "but the hard cap is %d. Choose only %d cells and "
                      "keep them in deep-night hours. Respond STRICT JSON "
                      "only." % (round_no, len(sleeping_cells), CELLS // 2,
                                 CELLS // 2))
            continue

        sim = dispatch_simulation(
            {"cells": CELLS, "windows": wins}, pid)
        score = score_simulation(sim)
        ok = (sim["savings_pct"] >= MIN_SAVINGS_PCT
              and sim["qos_dropped_pct"] <= MAX_QOS_DROP_PCT
              and score["energy_efficiency"] >= MIN_EFFICIENCY)
        verdict = {"round": round_no, "proposal": proposal,
                   "simulation": sim, "score": score, "accepted": ok}
        attempts.append(verdict)

        if mlflow:
            try:
                with mlflow.start_run(run_name=("accept-" if ok else
                                                "reject-") + pid):
                    mlflow.log_param("episode", episode)
                    mlflow.log_param("round", round_no)
                    mlflow.log_param("accepted", ok)
                    mlflow.log_metric("savings_pct", sim["savings_pct"])
                    mlflow.log_metric("qos_dropped_pct",
                                      sim["qos_dropped_pct"])
                    mlflow.log_metric("energy_efficiency",
                                      score["energy_efficiency"])
                    mlflow.log_dict(verdict, "attempt.json")
            except Exception as e:
                print("[mlflow] log skipped:", e, flush=True)

        if ok:
            plan = {"episode": episode, "accepted_round": round_no,
                    "windows": proposal.get("windows", []),
                    "rationale": proposal.get("rationale"),
                    "evidence": {"simulation": sim, "score": score},
                    "thresholds": {"min_savings_pct": MIN_SAVINGS_PCT,
                                   "max_qos_drop_pct": MAX_QOS_DROP_PCT,
                                   "min_efficiency": MIN_EFFICIENCY},
                    "attempts": attempts}
            if mlflow:
                try:
                    with mlflow.start_run(
                            run_name="change-plan-" + episode):
                        mlflow.log_param("episode", episode)
                        mlflow.log_param("rounds", round_no)
                        mlflow.log_metric("savings_pct",
                                          sim["savings_pct"])
                        mlflow.log_metric("co2_savings_kg",
                                          sim["co2_savings_kg"])
                        mlflow.log_dict(plan, "change_plan.json")
                except Exception as e:
                    print("[mlflow] log skipped:", e, flush=True)
            print("CHANGE_PLAN " + json.dumps(plan), flush=True)
            return plan

        prompt = ("REJECTED round %d: savings %.2f%% (need >= %.1f%%), "
                  "dropped %.3f%% (max %.2f%%), efficiency %.1f "
                  "(need >= %.1f). Revise the windows: %s. Respond "
                  "STRICT JSON only." % (
                      round_no, sim["savings_pct"], MIN_SAVINGS_PCT,
                      sim["qos_dropped_pct"], MAX_QOS_DROP_PCT,
                      score["energy_efficiency"], MIN_EFFICIENCY,
                      "extend night windows or add cells if savings "
                      "too low; shrink windows if QoS dropped"))
    print("NO_PLAN after", MAX_ROUNDS, "rounds", flush=True)
    return None


if __name__ == "__main__":
    run_episode()
