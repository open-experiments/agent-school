#!/usr/bin/env bash
# Agent School regression suite — serves docs/ locally and runs all Playwright suites,
# then verifies every course tape regenerates byte-identical from the raw Venice capture.
# Prereqs: node + `npm i playwright` (+ `npx playwright install chromium`, or set CHROMIUM_PATH).
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

PORT="${PORT:-8777}"
export BASE_URL="http://localhost:${PORT}"
python3 -m http.server "$PORT" --directory docs &>/dev/null &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 1

for t in site portal-core portal-copy portal-kinds portal-101-regen; do
  echo "=== docs/tests/${t}.test.js"
  node "docs/tests/${t}.test.js"
done

echo "=== tape regeneration stability"
TMP=$(mktemp -d)
for c in 101 201 202 301 302; do
  d=$(ls -d ${c}-*/)
  python3 docs/portal/build-tapes.py "$c" venice shared/tapes/venice-tape-raw.json.gz "${d}MANUAL.md" "$TMP/${c}.json" >/dev/null
  cmp -s "$TMP/${c}.json" "docs/portal/tapes/${c}-venice.json" && echo "PASS | $c tape regen byte-identical" || { echo "FAIL | $c tape drifts from generator"; exit 1; }
done
echo
echo "ALL SUITES GREEN"
