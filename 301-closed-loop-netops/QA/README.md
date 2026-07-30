# 301 · QA Evidence Pack (in progress)

The course is working and runs live on the Rome and Venice reference
clusters — current evidence lives outside this folder: the narrated
walkthrough ([../images/](../images/)), the RHOAI dashboard captures
([../images/rhoai/](../images/rhoai/)), and the time-snapshotted Venice
lab tape ([docs/portal/tapes/301-venice.json](../../docs/portal/tapes/301-venice.json)).
This pack collects the wire-level evidence to bring 301 to the 101/201
QA-passed bar; the matrix follows the 101 pattern, and every live run
added here must include wire-level evidence.

## Test matrix

| # | Test | Evidence | Status |
|---|------|----------|--------|
| 1 | Deterministic offline / mock-loop run | log | pending |
| 2 | Skill servers and backends invoked directly | log | pending |
| 3 | Live agent run vs model endpoint | trace log + wire JSONL (LLM_WIRE_LOG) | pending |
| 4 | External tool / model call traces (request + response bodies, auth redacted) | wire JSONL | pending |

See ../../101-noc-assistant/QA/ for the reference pack format.
