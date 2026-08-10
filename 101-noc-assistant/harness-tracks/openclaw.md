# Harness track: OpenClaw

Run the same NOC Assistant on [OpenClaw](https://github.com/openclaw/openclaw),
the always-on personal agent harness. No agent code is written here; that is
the lesson. The skills you already have as MCP servers plug into OpenClaw's
config, and its own loop takes over what `agent/noc_agent.py` demonstrated.

Config keys below are from the official OpenClaw docs
([gateway/config-tools](https://docs.openclaw.ai/gateway/config-tools)).
Config lives at `~/.openclaw/openclaw.json` (JSON5).

## 1. Point OpenClaw at your model endpoint

vLLM on RHOAI (or any OpenAI-compatible endpoint) is a custom provider with
`api: "openai-completions"`:

```json5
{
  models: {
    mode: "merge",
    providers: {
      rhoai: {
        baseUrl: "https://<your-inference-route>/v1",
        apiKey: "${RHOAI_API_KEY}",
        api: "openai-completions",
        models: [
          { id: "<deployed-model-name>", name: "RHOAI model",
            contextWindow: 128000, maxTokens: 8192 },
        ],
      },
    },
  },
  agents: {
    defaults: { model: { primary: "rhoai/<deployed-model-name>" } },
  },
}
```

## 2. Register the NOC skills as MCP servers

```json5
{
  mcp: {
    servers: {
      "noc-telemetry": {
        command: "python3",
        args: ["<repo-path>/101-noc-assistant/tools/telemetry_mcp.py"],
      },
      "noc-runbooks": {
        command: "python3",
        args: ["<repo-path>/101-noc-assistant/tools/runbook_mcp.py"],
      },
    },
  },
}
```

OpenClaw exposes these as plugin-owned tools (`noc-telemetry__get_kpi_summary`,
`noc-telemetry__detect_anomalies`, `noc-runbooks__search_runbooks`, ...).

## 3. If you run OpenClaw sandboxed, allow the MCP tools through

Sandboxed sessions filter tools with an extra gate. Add `bundle-mcp` to the
sandbox allowlist so the NOC tools stay visible:

```json5
{
  tools: {
    sandbox: {
      tools: { alsoAllow: ["bundle-mcp"] },
    },
  },
}
```

Run `openclaw doctor` to verify the shape.

## 4. Ask

Restart the gateway and ask through any connected channel or the TUI:

> What is wrong in the 5G core right now?

OpenClaw's loop calls the same Isolation Forest, alert feed, and runbook
tools over the same real 5gprod data, then answers.

## 5. Continuous operation (7/24 NOC watch)

The custom teaching loop is request-driven. OpenClaw's
[heartbeat](https://docs.openclaw.ai/gateway/heartbeat) turns the same setup
into an always-on monitor: it runs a periodic agent turn, and the agent
replies `HEARTBEAT_OK` when nothing needs attention or sends an alert when
something does. Omitting `activeHours` means 24/7 (the default).

Because the NOC tools re-read `data/*.csv` and `alerts.json` on every call,
fresh data dropped into the folder is picked up on the next heartbeat tick
automatically; no restart, no re-index. (In production you would swap the
file-backed tools for a live telemetry source; the heartbeat side does not
change.)

```json5
{
  agents: {
    defaults: {
      heartbeat: {
        every: "15m",
        target: "last",          // or an explicit channel, e.g. "telegram"
        isolatedSession: true,   // fresh session per tick: cheap, and 12-Factor clean
        lightContext: true,      // only HEARTBEAT.md from workspace bootstrap
        prompt: "Run noc-telemetry__get_active_alerts and noc-telemetry__detect_anomalies. If there is a CRITICAL alert or a new anomaly cluster, send a short NOC alert naming the NF, the deviating KPIs, and the matching runbook. Otherwise reply HEARTBEAT_OK.",
      },
    },
  },
}
```

For several cadences in one agent, use a `tasks:` block in `HEARTBEAT.md`
(only due tasks run each tick; no-due ticks are skipped to save tokens):

```markdown
tasks:

- name: anomaly-watch
  interval: 15m
  prompt: "Run detect_anomalies on all NFs; alert if a new anomaly cluster appears."
- name: alert-feed-scan
  interval: 5m
  prompt: "Check get_active_alerts; alert on any new CRITICAL entry."
```

Note `isolatedSession: true` is the 12-Factor Agent discipline applied by the
product harness: each tick starts with a clean context, and state worth
keeping lives outside the session.

## What to notice

1. Zero agent code changed hands. The harness slot in Table-1 of our article
   is genuinely swappable when skills live behind MCP.
2. What you give up against the custom loop: the readable, fixed plan. What
   you gain: channels, memory, scheduling, and an always-on loop you did not
   have to write.
3. Tool governance carries over: put an MCP gateway in front of these two
   servers and OpenClaw's calls get claims-checked like everyone else's.
4. Going further, NVIDIA's NemoClaw can run this exact harness inside an
   OpenShell sandbox; that is the planned 301 product-harness track, not
   something the repo ships today.
