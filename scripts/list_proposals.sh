#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d proposals ]; then
  echo "No proposals folder."
  exit 0
fi

echo "== Proposals =="
ls -1 proposals 2>/dev/null | sort -r | while read -r d; do
  [ -d "proposals/$d" ] || continue
  status="unknown"
  if [ -f "proposals/$d/status.json" ]; then
    status="$(python3 - <<PY
import json
p="proposals/$d/status.json"
try:
  j=json.load(open(p,"r",encoding="utf-8"))
  print(j.get("status","unknown"))
except Exception:
  print("broken")
PY
)"
  fi
  echo "$d  [$status]"
done
