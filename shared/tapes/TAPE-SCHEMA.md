# Course tape schema (portal player format)

The portal is a static single-page app on GitHub Pages that replays recorded reality: a student performs each manual step in a simulated console and watches the recorded consequences. This document defines the "course tape" the player consumes and how tapes are derived from the raw cluster snapshots in this directory.

## Design rules

1. Everything shown is captured, never generated. A tape is a slice-and-order of the raw snapshot plus recorded terminal transcripts. If we did not capture it, the player does not show it.
2. One tape per course, one step per manual step. The tape's steps mirror `MANUAL.md` exactly (same numbering, same Why/Do/Expect text), so the manual and the portal stay in lockstep by construction.
3. Deterministic and scrubbable. Every step declares the complete visible state after it, not a diff chain, so the player can jump to any step directly.
4. Honest failure modes. Steps may carry optional `wrongTurns`: recorded errors from the EA findings (the build stuck in `New`, the workflow never reconciling) that play when a student acts out of order.

## Tape file

`docs/portal/tapes/<course>-<cluster>.json`

```json
{
  "tapeVersion": 1,
  "course": "101",
  "title": "NOC Assistant",
  "cluster": "venice",
  "capturedAt": "2026-07-28T19:45:00Z",
  "steps": [ Step, ... ],
  "assets": {
    "resources": { "<ns>/<kind>/<name>": { full object } },
    "logs": { "<ns>/<pod>/<container>": "timestamped text" },
    "mlflow": { "experiments": [...], "runs": {...} },
    "registry": [ ... ]
  }
}
```

`assets` is the pool (extracted from the raw tape); `steps` reference into it by key so nothing is duplicated.

## Step object

```json
{
  "id": "101-3",
  "title": "Base workload (build + agent identity)",
  "why": "markdown, verbatim from MANUAL.md",
  "action": {
    "kind": "import-yaml | start-build | create-configmap | create-job | set-env | wait",
    "label": "what the student clicks or types",
    "yaml": "the manifest text shown in the simulated editor (when kind=import-yaml)",
    "terminalCmd": "the oc fallback one-liner"
  },
  "playback": {
    "terminal": [ {"t": 0, "text": "line"}, ... ],
    "podsAppear": ["agent-school/pods/noc-assistant-1-build"],
    "logStream": {"key": "agent-school/noc-ask-jb2qw-d7mqw/agent", "speed": "recorded | fast"},
    "dashboard": {
      "panel": "experiments | pipelines | registry | deployments | featurestore | workloads",
      "assets": ["mlflow.experiments[name=101-noc-assistant]"]
    }
  },
  "expect": {
    "text": "the Expect block from MANUAL.md",
    "check": {"assetKey": "agent-school/jobs/feast-bootstrap", "path": "status.succeeded", "equals": 1}
  },
  "wrongTurns": [
    {"ifBefore": "101-2", "terminal": "recorded error text", "lesson": "one sentence pointing at the prerequisite"}
  ]
}
```

The `expect.check` makes each step a game gate: the player evaluates it against the assets it just revealed, and the step is "won" when it passes. `playback.terminal` entries carry millisecond offsets reconstructed from log timestamps so streams replay with real pacing (with a fast-forward control).

## Player panes

Three panes, matching the walkthrough videos' visual language:

- Manual pane: the step's Why/Do/Expect, rendered from the tape (source of truth stays MANUAL.md; the tape build copies it in).
- Console pane: a simulated terminal plus a minimal Import YAML dialog. Student action here advances the tape.
- Platform pane: HTML re-renderings of the dashboard surfaces from captured JSON: an Experiments table (from `assets.mlflow`), a pipeline DAG (from the captured Argo Workflow status tree), the registry entries, the Deployments/InferenceServices list with Ready conditions, Kueue workloads. Interactive and searchable because it is data, not screenshots.

## Build pipeline (raw tape to course tape)

A small generator script (`docs/portal/build-tapes.py`, to be written with the player) does, per course:

1. Parse `MANUAL.md` into steps (Why/Do/Expect blocks).
2. Select the asset subset for the course from the raw snapshot (by namespace + name patterns per course).
3. Attach recorded terminal transcripts: taken from captured Job pod logs where the pod survived; for pods that TTL-expired before capture, from the session transcripts in `shared/manifests/venice/PROGRESS.md` era records, marked `"reconstructed": true`.
4. Emit the tape JSON plus a size report (target: under 2 MB per course tape after trimming logs to the interesting windows).

## Status

- Raw snapshots: captured and committed (this directory), both clusters, July 28, 2026.
- Next: build `101-venice.json` by hand-running the generator steps once, then the player MVP against it; then generalize to the other four courses.

> **Maintenance note (QA 2026-07-28):** `docs/portal/build-tapes.py` regenerates courses 201–302 byte-identical to the shipped tapes. Course 101's shipped tape was hand-finished after generation (cmds-format steps, `kimi-isvc` asset, `exp:` reveal gating); port `build_101` to the cmds format before regenerating it.
