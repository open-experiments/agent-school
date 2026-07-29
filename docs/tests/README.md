# Regression suite

Playwright end-to-end checks for the Agent School site and the interactive lab portal,
born out of the July 2026 QA rounds (findings F1–F14). Run before pushing changes to
`docs/` or the tapes.

## Run everything

```bash
npm i playwright && npx playwright install chromium   # once
docs/tests/run.sh
```

Set `CHROMIUM_PATH` to use a pre-installed Chromium instead of Playwright's download,
and `PORT` to serve on something other than 8777.

## What each suite covers

| Suite | Covers |
|---|---|
| `site.test.js` | Course cards (durations, badges, links), detail pages, publications, scroll behavior |
| `portal-core.test.js` | Lab player mechanics: Take-me-there tab switching (F4), spoiler gating (F5), course-LLM row (F8), full 201 E2E, bad-tape handling |
| `portal-copy.test.js` | Traces-not-runs guide copy (F2), footnotes (F10), post-edit E2E regressions |
| `portal-kinds.test.js` | Kind rendering in Projects course-objects (F13) across all tapes |
| `portal-101-regen.test.js` | Full 7-step 101 E2E on the generator-produced tape (F12) |
| `run.sh` (final stage) | All five tapes regenerate byte-identical from `shared/tapes/venice-tape-raw.json.gz` |
