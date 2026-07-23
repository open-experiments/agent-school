"""Cell-sleep simulation — the 302 agent's pattern-3 skill backend.

Simulate-before-act: the agent proposes sleep windows, and this Job
computes what they would do BEFORE anything touches a network. The
power model and cost model are vendored from the Telco-AIX
`airan-energy` experiment (src/models/energy_calculator.py: active
1000W full / 700W half / 500W idle, light sleep 100W, wake transition
200W for 2 min; $0.12/kWh, 0.5 kg CO2/kWh). The traffic profile is
the same diurnal shape the experiment's dataset generator uses —
deterministic, seeded, documented.

The simulation core is JAX (jax.numpy, CPU): a vectorized 24h sweep at
15-minute steps across all cells at once. Input is the proposal JSON
(env PROPOSAL): {"cells": N, "windows": [{"cell": i, "start_hour": h0,
"end_hour": h1}]}. QoS guard is part of the physics, not the prompt:
traffic arriving at a sleeping cell is re-homed to awake neighbors
while they have capacity; what cannot be re-homed is counted as
dropped and reported — the agent's threshold gate decides what to do
with that number.

Prints one SIM_RESULT JSON line (the agent parses this from the Job
log) and mirrors everything to MLflow when configured.

Runs as a Kueue-admitted Job — see deploy/ocp/rome/job-sim-template.yaml.
"""
import json
import os
from pathlib import Path

import jax.numpy as jnp
import numpy as np

# ---- power + cost model: airan-energy PowerModel/CostModel values ----
P_FULL, P_HALF, P_IDLE = 1000.0, 700.0, 500.0     # W (active, by load)
P_SLEEP = 100.0                                    # W (light sleep)
P_WAKE, WAKE_MIN = 200.0, 2.0                      # W extra, minutes
KWH_COST, CO2_PER_KWH = 0.12, 0.5                  # $/kWh, kg/kWh
CELL_CAPACITY = 1000.0                             # Mbps per cell
STEP_MIN = 15.0
STEPS = int(24 * 60 / STEP_MIN)


def diurnal_traffic(n_cells, seed=42):
    """The airan-energy dataset generator's diurnal shape: night
    trough, morning ramp, evening peak; per-cell scale + noise,
    deterministic under the seed."""
    rng = np.random.default_rng(seed)
    hours = np.arange(STEPS) * STEP_MIN / 60.0
    base = (0.25
            + 0.35 * np.exp(-((hours - 9.5) ** 2) / 8.0)
            + 0.55 * np.exp(-((hours - 20.0) ** 2) / 6.0))
    scale = rng.uniform(0.6, 1.0, size=n_cells)
    noise = rng.normal(0, 0.03, size=(n_cells, STEPS))
    profile = np.clip(base[None, :] * scale[:, None] + noise, 0.02, 1.0)
    return profile * CELL_CAPACITY  # Mbps per cell per step


def power_watts(traffic, sleeping):
    """airan-energy linear-by-load power curve, vectorized in JAX."""
    util = jnp.clip(traffic / CELL_CAPACITY, 0.0, 1.0)
    active = jnp.where(
        util >= 0.5,
        P_HALF + (util - 0.5) * 2.0 * (P_FULL - P_HALF),
        P_IDLE + util * 2.0 * (P_HALF - P_IDLE))
    return jnp.where(sleeping, P_SLEEP, active)


def simulate(proposal):
    n = int(proposal.get("cells", 6))
    traffic = jnp.array(diurnal_traffic(n))

    sleeping = np.zeros((n, STEPS), dtype=bool)
    for w in proposal.get("windows", []):
        c = int(w["cell"])
        if not (0 <= c < n):
            continue
        h0, h1 = float(w["start_hour"]), float(w["end_hour"])
        idx = np.arange(STEPS) * STEP_MIN / 60.0
        mask = (idx >= h0) & (idx < h1) if h0 <= h1 else \
               (idx >= h0) | (idx < h1)          # windows may wrap midnight
        sleeping[c] |= mask
    sleeping = jnp.array(sleeping)

    # QoS physics: traffic of sleeping cells re-homes to awake cells'
    # spare capacity within the same step; the remainder is dropped.
    awake = ~sleeping
    served_awake = jnp.where(awake, traffic, 0.0)
    to_rehome = jnp.where(sleeping, traffic, 0.0).sum(axis=0)
    spare = (CELL_CAPACITY - served_awake) * awake
    spare_total = jnp.clip(spare.sum(axis=0), 0.0, None)
    rehomed = jnp.minimum(to_rehome, spare_total)
    dropped = to_rehome - rehomed

    # power: sleeping cells at sleep power; awake cells carry their own
    # traffic plus a proportional share of the re-homed load.
    share = jnp.where(spare_total[None, :] > 0,
                      spare / jnp.where(spare_total[None, :] == 0, 1.0,
                                        spare_total[None, :]), 0.0)
    eff_traffic = served_awake + share * rehomed[None, :]
    p_opt = power_watts(eff_traffic, sleeping)

    # wake-up transitions: entering awake from sleep costs P_WAKE for
    # WAKE_MIN minutes (airan-energy transition model).
    trans = jnp.diff(sleeping.astype(jnp.int8), axis=1, prepend=0) == -1
    p_opt = p_opt + trans * (P_WAKE * WAKE_MIN / STEP_MIN)

    p_base = power_watts(traffic, jnp.zeros_like(sleeping))

    step_h = STEP_MIN / 60.0
    base_kwh = float(p_base.sum() * step_h / 1000.0)
    opt_kwh = float(p_opt.sum() * step_h / 1000.0)
    total_mbps_steps = float(traffic.sum())
    dropped_total = float(dropped.sum())
    return {
        "cells": n,
        "windows": proposal.get("windows", []),
        "baseline_kwh": round(base_kwh, 2),
        "optimized_kwh": round(opt_kwh, 2),
        "savings_kwh": round(base_kwh - opt_kwh, 2),
        "savings_pct": round(100.0 * (base_kwh - opt_kwh) / base_kwh, 2),
        "cost_savings_usd": round((base_kwh - opt_kwh) * KWH_COST, 2),
        "co2_savings_kg": round((base_kwh - opt_kwh) * CO2_PER_KWH, 2),
        "qos_dropped_pct": round(100.0 * dropped_total /
                                 total_mbps_steps, 3),
        "power_model": "airan-energy PowerModel (1000/700/500W active, "
                       "100W sleep, 200W x 2min wake)",
    }


def maybe_mlflow(result, proposal_id):
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    ws = os.environ.get("MLFLOW_WORKSPACE")
    if not uri or not ws:
        return
    try:
        os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
        tok = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        if tok.exists():
            os.environ["MLFLOW_TRACKING_TOKEN"] = tok.read_text().strip()
        from mlflow.utils import rest_utils
        orig = rest_utils.http_request

        def shim(*a, **kw):
            h = dict(kw.pop("extra_headers", None) or {})
            h["X-MLFLOW-WORKSPACE"] = ws
            kw["extra_headers"] = h
            return orig(*a, **kw)

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
        with mlflow.start_run(run_name="sim-" + proposal_id):
            mlflow.log_param("proposal_id", proposal_id)
            mlflow.log_param("windows", json.dumps(result["windows"])[:490])
            for k in ("baseline_kwh", "optimized_kwh", "savings_pct",
                      "qos_dropped_pct", "cost_savings_usd",
                      "co2_savings_kg"):
                mlflow.log_metric(k, result[k])
            mlflow.log_dict(result, "simulation.json")
        print("[mlflow] sim logged", flush=True)
    except Exception as e:
        print("[mlflow] skipped:", type(e).__name__, e, flush=True)


if __name__ == "__main__":
    proposal = json.loads(os.environ.get("PROPOSAL", '{"cells": 6}'))
    proposal_id = os.environ.get("PROPOSAL_ID", "manual")
    result = simulate(proposal)
    maybe_mlflow(result, proposal_id)
    print("SIM_RESULT " + json.dumps(result), flush=True)
