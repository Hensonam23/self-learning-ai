#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

API_UNIT="machinespirit-api.service"
UI_UNIT="machinespirit-ui.service"

want_port () {
  local port="$1"
  ss -ltnp | grep -qE ":${port}\b"
}

wait_port () {
  local port="$1"
  local label="$2"
  local tries=40   # 40 * 0.25s = 10s
  for ((i=1;i<=tries;i++)); do
    if want_port "$port"; then
      echo "OK: $label (${port}) is listening"
      return 0
    fi
    sleep 0.25
  done
  echo "FAIL: $label (${port}) not listening after 10s"
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
  local unit="$1"
  local port="$2"
  local label="$3"

  # If not listening, try restart
  if ! want_port "$port"; then
    echo "INFO: $label not listening yet — restarting $unit"
    systemctl --user restart "$unit" || true
  fi

  if ! wait_port "$port" "$label"; then
    dump_debug "$unit"
    return 1
  fi
  return 0
}

echo "== selftest: python compile =="
python3 -m py_compile ms_api.py ms_ui.py brain.py

echo "== selftest: ensure services listening =="
ensure_service "$API_UNIT" 8010 "API" || exit 1
ensure_service "$UI_UNIT"  8020 "UI"  || exit 1

echo "== selftest: /health =="
MS_API_KEY="$(grep -m1 '^MS_API_KEY=' ~/.config/machinespirit/secrets.env | cut -d= -f2-)"
curl -fsS http://127.0.0.1:8010/health -H "X-API-Key: $MS_API_KEY" >/dev/null
curl -fsS http://127.0.0.1:8020/health >/dev/null

echo "== selftest: local facts =="
check_ok () {
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

check_ok "what time is it?"
check_ok "what is the date?"
check_ok "what day is it?"
check_ok "what is my name?"
check_ok "what is your name?"

echo "PASS: selftest complete"
