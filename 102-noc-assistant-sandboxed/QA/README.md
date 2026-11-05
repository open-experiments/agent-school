# 102 · QA Evidence Pack (empty — capture not yet run)

This folder is deliberately empty of results. The course claims demonstrated
constraint, so until a real sandbox refuses a real access, there is nothing
here to read. The matrix below is the plan, and every row is `pending`.

Format follows [101's pack](../../101-noc-assistant/QA/README.md), which is
the reference bar: captured output only, nothing hand-written, wire-level
evidence for anything that crosses a network boundary.

## Test matrix

| # | Test | Evidence | Status |
|---|------|----------|--------|
| 1 | Sandbox created from `openshell/policy.yaml`; applied policy read back | `policy_applied.log` (`openshell policy get`) | pending |
| 2 | Landlock enforcement level actually obtained on RHEL CoreOS 9.8 (kernel 5.14) under `best_effort` | `landlock_level.log` | pending |
| 3 | Agent answers the 101 NOC question inside the sandbox | `sandboxed_run_trace.log` | pending |
| 4 | **Denial — filesystem:** read of `~/.ssh`, write outside `/sandbox` | `deny_filesystem.log` | pending |
| 5 | **Denial — network:** egress to a host absent from policy | `deny_network.log` | pending |
| 6 | **Denial — hot reload:** policy tightened on a running sandbox; next tool call refused | `deny_hotreload.log` | pending |
| 7 | **Credential absence:** sandbox env + filesystem searched for `LLM_API_KEY`, empty, while inference succeeds | `no_credential.log` + wire capture | pending |
| 8 | MCP-over-stdio across the sandbox boundary: do 101's two skill servers work unchanged? | `mcp_boundary.log` | pending |
| 9 | Cost of the boundary: sandbox cold start and steady-state overhead vs 101's plain pod | `overhead.log` | pending |

Tests 4–7 are the course. A green row 3 with red rows 4–6 would mean the
sandbox is decorative, which is a result worth publishing too.

## Test 8 is the one with an unknown answer

Rows 1–7 are expected to pass; they verify a vendor's documented behavior in
our environment. Row 8 is genuinely open — the NemoClaw architecture
documentation covers managed integrations and policy-gated external APIs but
does not say how stdio MCP servers are handled inside a sandbox. Both
outcomes teach something:

- **Stdio survives** → the skill layer of every course in this repo transfers
  into a sandbox unchanged, and "tools go through MCP" holds without caveat.
- **Stdio does not survive** → skills must move behind the gateway as network
  integrations, and the convention needs an honest footnote. That footnote
  would apply to 101, 201, 301 and 302 as well.

## Capture blockers (as of August 2026)

The Venice reference cluster's two GPUs are held by an unrelated benchmark
(`gsma-otel2-test`), leaving `telco-aix/kimi-linear-48b-a3b` `READY=False`.
Rows 3 and 6–8 need a model endpoint. Two ways forward, in preference order:

1. Capture after the benchmark releases a GPU and the Kimi endpoint returns.
2. Point the OpenShell provider at another OpenAI-compatible endpoint. The
   benchmark's own served models would work technically, but agent traffic
   would contaminate the benchmark's numbers, so this needs an explicit
   go-ahead rather than an assumption.

Rows 1, 2, 4, 5 and 9 need no model and can be captured before then.
