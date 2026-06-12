# 301 · QA Evidence Pack (pending)

This course is planned; the QA pack fills as it is built. The matrix follows
the 101 pattern, and every live run must include wire-level evidence.

## Planned test matrix

| # | Test | Evidence | Status |
|---|------|----------|--------|
| 1 | Deterministic offline / mock-loop run | log | pending |
| 2 | Skill servers and backends invoked directly | log | pending |
| 3 | Live agent run vs model endpoint | trace log + wire JSONL (LLM_WIRE_LOG) | pending |
| 4 | External tool / model call traces (request + response bodies, auth redacted) | wire JSONL | pending |

See ../../101-noc-assistant/QA/ for the reference pack format.
