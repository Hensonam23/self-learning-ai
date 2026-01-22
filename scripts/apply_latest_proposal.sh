#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

pick_latest_pending() {
  ls -1 proposals 2>/dev/null | sort -r | while read -r d; do
    [ -d "proposals/$d" ] || continue
    p="proposals/$d/status.json"
    [ -f "$p" ] || continue
    st="$(python3 - <<PY
import json
try:
  j=json.load(open("$p","r",encoding="utf-8"))
  print(j.get("status",""))
except Exception:
  print("")
PY
)"
    if [ "$st" = "pending" ]; then
      echo "proposals/$d"
      return 0
    fi
  done
  return 1
}

DIR="$(pick_latest_pending || true)"
if [ -z "${DIR:-}" ]; then
  echo "No pending proposals."
  exit 0
fi

./scripts/apply_proposal.sh "$DIR"
