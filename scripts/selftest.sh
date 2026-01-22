#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

API_UNIT="machinespirit-api.service"
UI_UNIT="machinespirit-ui.service"

want_port () { ss -ltnp | grep -qE ":$1\b"; }

wait_port () {
  local port="$1" label="$2"
  local tries=40  # 10s total
  for ((i=1;i<=tries;i++)); do
    if want_port "$port"; then
      echo "OK: $label ($port) is listening"
      return 0
    fi
    sleep 0.25
  done
  echo "FAIL: $label ($port) not listening after 10s"
  return 1
}

dump_debug () {
  local unit="$1"
  echo
  echo "== DEBUG: systemctl status $unit =="
  systemctl --user status "$unit" --no-pager -l || true
  echo
  echo "== DEBUG: journalctl last 120 lines for $unit =="
  journalctl --user -u "$unit" -n 120 --no-pager || true
  echo
}

ensure_service () {
  local unit="$1" port="$2" label="$3"
  if ! want_port "$port"; then
    echo "INFO: $label not listening yet — restarting $unit"
    systemctl --user restart "$unit" || true
  fi
  if ! wait_port "$port" "$label"; then
    dump_debug "$unit"
    return 1
  fi
}

echo "== selftest: python compile =="
python3 -m py_compile ms_api.py ms_ui.py brain.py

echo "== selftest: ensure services listening =="
ensure_service "$API_UNIT" 8010 "API"
ensure_service "$UI_UNIT"  8020 "UI"

echo "== selftest: /health =="
MS_API_KEY="$(grep -m1 '^MS_API_KEY=' ~/.config/machinespirit/secrets.env | cut -d= -f2-)"
curl -fsS http://127.0.0.1:8010/health -H "X-API-Key: $MS_API_KEY" >/dev/null
curl -fsS http://127.0.0.1:8020/health >/dev/null

echo "== selftest: local facts (API direct) =="
check_api_ok () {
  local q="$1"
  local out
  out="$(curl -fsS http://127.0.0.1:8010/ask \
    -H "X-API-Key: $MS_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"$q\"}")"
  python3 - <<PY
import json
j=json.loads("""$out""")
assert j.get("ok") is True, j
assert isinstance(j.get("answer",""), str) and j.get("answer","").strip(), j
print("OK:", j.get("topic"), "=>", j.get("answer")[:80].replace("\\n"," "))
PY
}

check_api_ok "what time is it?"
check_api_ok "what is the date?"
check_api_ok "what day is it?"
check_api_ok "what is my name?"
check_api_ok "what is your name?"

echo "== selftest: UI proxy JSON + override + pinned behavior =="
TS="$(date +%s)"
TOPIC="__selftest_override_${TS}__"
ANSWER="SELFTEST: override works"

# 1) UI /api/ask must return JSON (and not crash on decode)
UI_ASK_OUT="$(curl -fsS http://127.0.0.1:8020/api/ask \
  -H "Content-Type: application/json" \
  -d '{"text":"what is my name?"}')"

python3 - <<PY
import json
j=json.loads("""$UI_ASK_OUT""")
assert isinstance(j, dict), j
# UI proxy format can vary, but it must be valid JSON and usually has ok/topic/answer
print("OK: UI /api/ask returned JSON keys:", sorted(list(j.keys()))[:12])
PY

# 2) UI /api/override must succeed
UI_OVR_OUT="$(curl -fsS http://127.0.0.1:8020/api/override \
  -H "Content-Type: application/json" \
  -d "{\"topic\":\"$TOPIC\",\"answer\":\"$ANSWER\"}")"

python3 - <<PY
import json
j=json.loads("""$UI_OVR_OUT""")
assert j.get("ok") is True, j
print("OK: UI override saved topic:", j.get("topic"))
PY

# 3) API must return the pinned override (pinned always wins)
API_OUT="$(curl -fsS http://127.0.0.1:8010/ask \
  -H "X-API-Key: $MS_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"what is $TOPIC?\"}")"

python3 - <<PY
import json
j=json.loads("""$API_OUT""")
ans=j.get("answer","")
assert j.get("ok") is True, j
assert "SELFTEST: override works" in ans, j
print("OK: pinned answer returned")
PY

# 4) Cleanup (remove the selftest entry from local_knowledge.json)
python3 - <<PY
import json
from pathlib import Path

p=Path("data/local_knowledge.json")
if not p.exists():
    raise SystemExit("WARN: data/local_knowledge.json not found, skipping cleanup")

db=json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
if not isinstance(db, dict):
    raise SystemExit("WARN: local_knowledge not a dict, skipping cleanup")

key="$TOPIC".strip().lower()
if key in db:
    del db[key]
    p.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("OK: cleaned up", key)
else:
    print("OK: cleanup not needed (key not found)")
PY

echo "PASS: selftest complete"
