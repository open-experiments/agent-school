# 202 · QA Evidence Pack

The matrix follows the 101 pattern. Live evidence is from Rome (July 2026).

| # | Test | Evidence | Status |
|---|------|----------|--------|
| 1 | Deterministic offline run — full graph over 6 real dataset rows (4 legit, 2 fraud), stub scorer, both gate arms scripted | [offline_triage_run.log](./offline_triage_run.log) — `TRIAGE_OK {"clear": 3, "hold": 1, "escalated": 1, "released_to_hold": 1}` | pass |
| 2 | Served-model tool invoked directly | live V2 smoke ([job-infer-smoke.yaml](../deploy/ocp/rome/job-infer-smoke.yaml)) — `INFER_OK`, served verdicts vs truth | pass |
| 3 | Live agent run, negative gate proof (no approve token) | Job `fraud-triage` on Rome — served model scored legit cases 0.0 (cleared) and fraud cases 1.0 (escalated); both escalations parked: `TRIAGE_OK {"clear": 4, "awaiting_approval": 2}` | pass |
| 4 | Live agent run, approved path | same Job with APPROVE_TOKEN — gate resumed with approver identity: `TRIAGE_OK {"clear": 4, "escalated": 2}`; 12 `triage-case-*` audit runs in the `revassurance-fraud` experiment (see ../images/rhoai/triage-cases.png) | pass |

The live case audits are platform data: each run carries decision, outcome,
decided_by, approval_required, approver, scorer, and the fraud_probability
metric, with the full case record as an artifact.
