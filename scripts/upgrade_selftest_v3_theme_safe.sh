#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

python3 - <<'PY'
from pathlib import Path

content = r'''#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

echo "== selftest: python compile =="
python3 -m py_compile ms_api.py ms_ui.py brain.py scripts/auto_propose.py

need_port () {
  local port="$1"
  local svc="$2"
  local tries=20

  for i in $(seq 1 "$tries"); do
    if ss -ltnp 2>/dev/null | grep -q ":${port}\b"; then
      echo "OK: ${svc} (${port}) is listening"
      return 0
    fi
    if [ "$i" -eq 1 ]; then
      echo "INFO: ${svc} not listening yet — restarting ${svc}.service"
      systemctl --user restart "${svc}.service" >/dev/null 2>&1 || true
    fi
    sleep 0.5
  done

  echo "FAIL: ${svc} (${port}) not listening after ${tries} tries"
  ss -ltnp | grep -E ":(8010|8020)\b" || true
  systemctl --user status machinespirit-api.service machinespirit-ui.service --no-pager -l || true
  exit 1
}

curl_body () {
  local url="$1"
  shift
  local out rc
  set +e
  out="$(curl -fsS "$url" "$@")"
  rc=$?
  set -e

  if [ $rc -ne 0 ]; then
    echo "FAIL: curl rc=$rc url=$url" >&2
    echo "DEBUG http_code: $(curl -sS -o /dev/null -w '%{http_code}' "$url" "$@" 2>/dev/null || true)" >&2
    return $rc
  fi

  if [ -z "$out" ]; then
    echo "FAIL: empty response from url=$url" >&2
    echo "DEBUG http_code: $(curl -sS -o /dev/null -w '%{http_code}' "$url" "$@" 2>/dev/null || true)" >&2
    return 1
  fi

  printf '%s' "$out"
}

mk_payload_text () {
  python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}, ensure_ascii=False))' "$1"
}

mk_payload_override () {
  python3 -c 'import json,sys; print(json.dumps({"topic": sys.argv[1], "answer": sys.argv[2]}, ensure_ascii=False))' "$1" "$2"
}

parse_ok () {
  python3 -c '
import json,sys
raw=sys.stdin.buffer.read()
try:
    j=json.loads(raw)
except Exception as e:
    print("FAIL: JSON decode error:", e, file=sys.stderr)
    print("RAW repr:", repr(raw[:240]), file=sys.stderr)
    try:
        print("RAW text:", raw.decode("utf-8","backslashreplace")[:800], file=sys.stderr)
    except Exception:
        pass
    sys.exit(2)
if j.get("ok") is not True:
    print("FAIL: ok != true:", j, file=sys.stderr)
    sys.exit(3)
ans=j.get("answer","")
if not isinstance(ans,str) or not ans.strip():
    print("FAIL: empty answer:", j, file=sys.stderr)
    sys.exit(4)
print("OK:", j.get("topic"), "=>", ans[:60].replace("\\n"," "))
'
}

echo "== selftest: ensure services listening =="
need_port 8010 machinespirit-api
need_port 8020 machinespirit-ui

echo "== selftest: /health =="
MS_API_KEY="$(grep -m1 '^MS_API_KEY=' ~/.config/machinespirit/secrets.env | cut -d= -f2-)"
curl -fsS "http://127.0.0.1:8010/health" -H "X-API-Key: $MS_API_KEY" >/dev/null
curl -fsS "http://127.0.0.1:8020/health" >/dev/null

echo "== selftest: local facts (API direct) =="
for q in "what time is it?" "what is the date?" "what day is it?" "what is my name?" "what is your name?"; do
  payload="$(mk_payload_text "$q")"
  curl_body "http://127.0.0.1:8010/ask" \
    -H "X-API-Key: $MS_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$payload" | parse_ok
done

echo "== selftest: UI proxy JSON + override + pinned behavior =="
payload="$(mk_payload_text "what time is it?")"
curl_body "http://127.0.0.1:8020/api/ask" \
  -H "Content-Type: application/json" \
  -d "$payload" | python3 -c '
import json,sys
j=json.loads(sys.stdin.buffer.read())
assert j.get("ok") is True, j
print("OK: UI /api/ask JSON keys:", sorted(list(j.keys())))
'

topic="__selftest_override_$(date +%s)__"
ov="$(mk_payload_override "$topic" "PINNED_TEST")"
curl_body "http://127.0.0.1:8020/api/override" \
  -H "Content-Type: application/json" \
  -d "$ov" | python3 -c '
import json,sys
j=json.loads(sys.stdin.buffer.read())
assert j.get("ok") is True, j
print("OK: UI override saved topic:", j.get("topic"))
'

ask="$(mk_payload_text "$topic")"
curl_body "http://127.0.0.1:8020/api/ask" \
  -H "Content-Type: application/json" \
  -d "$ask" | python3 -c '
import json,sys
j=json.loads(sys.stdin.buffer.read())
assert j.get("ok") is True, j
ans=j.get("answer","")
assert "PINNED_TEST" in ans, j   # theme may wrap, so we check contains
print("OK: pinned answer returned (contains PINNED_TEST)")
'

ov2="$(mk_payload_override "$topic" "CLEANUP_DONE")"
curl_body "http://127.0.0.1:8020/api/override" \
  -H "Content-Type: application/json" \
  -d "$ov2" >/dev/null
echo "OK: cleaned up $topic"

echo "PASS: selftest complete"
'''

Path("scripts/selftest.sh").write_text(content, encoding="utf-8")
print("OK: wrote scripts/selftest.sh")
PY

chmod +x scripts/selftest.sh
bash -n scripts/selftest.sh
echo "OK: selftest.sh parses clean"
