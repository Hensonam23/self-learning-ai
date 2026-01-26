#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-10.0.0.4}"
BASE="https://${HOST}"

pass() { echo "✅ $*"; }
fail() { echo "❌ $*"; exit 1; }

code() {
  curl -k -s -o /dev/null -w "%{http_code}" "$@"
}

echo "== MachineSpirit smoke test =="
echo "Target: $BASE"
echo

# 1) UI page should load (GET /ui)
UI_CODE="$(code "${BASE}/ui")"
[[ "$UI_CODE" == "200" ]] || fail "/ui expected 200, got ${UI_CODE}"
pass "/ui 200"

# 2) Health should respond
HEALTH_CODE="$(code "${BASE}/health")"
[[ "$HEALTH_CODE" == "200" ]] || fail "/health expected 200, got ${HEALTH_CODE}"
pass "/health 200"

# 3) Theme endpoint should respond
THEME_CODE="$(code "${BASE}/api/theme")"
[[ "$THEME_CODE" == "200" ]] || fail "/api/theme expected 200, got ${THEME_CODE}"
pass "/api/theme 200"

# 4) Ask should work (normal user)
ASK_CODE="$(curl -k -s -o /tmp/ms_ask.json -w "%{http_code}" \
  -H "Content-Type: application/json" \
  -d '{"text":"what is nat"}' \
  "${BASE}/api/ask")"

[[ "$ASK_CODE" == "200" ]] || fail "/api/ask expected 200, got ${ASK_CODE}"
grep -q '"ok":true' /tmp/ms_ask.json || fail "/api/ask did not return ok:true"
pass "/api/ask 200 + ok:true"

# 5) Override should be admin-only (401 without creds)
OVERRIDE_CODE="$(curl -k -s -o /dev/null -w "%{http_code}" \
  -H "Content-Type: application/json" \
  -d '{"topic":"x","answer":"y"}' \
  "${BASE}/api/override")"

[[ "$OVERRIDE_CODE" == "401" ]] || fail "/api/override expected 401 (no creds), got ${OVERRIDE_CODE}"
pass "/api/override 401 without creds (admin-only enforced)"

echo
pass "Smoke test PASSED"
