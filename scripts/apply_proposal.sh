#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 1 ]; then
  echo "Usage: ./scripts/apply_proposal.sh proposals/<folder>"
  exit 2
fi

DIR="$1"
if [ ! -d "$DIR" ]; then
  echo "ERROR: proposal dir not found: $DIR"
  exit 2
fi
if [ ! -x "$DIR/apply.sh" ]; then
  echo "ERROR: missing executable apply script: $DIR/apply.sh"
  exit 2
fi
if [ ! -f "$DIR/status.json" ]; then
  echo "ERROR: missing status.json: $DIR/status.json"
  exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$DIR/apply_${STAMP}.log"

echo "== apply_proposal =="
echo "Proposal: $DIR"
echo "Log: $LOG"
echo

# mark status -> applying
python3 - <<PY
import json, datetime
p="$DIR/status.json"
j=json.load(open(p,"r",encoding="utf-8"))
j["status"]="applying"
j["apply_started_at"]=datetime.datetime.now().isoformat(timespec="seconds")
open(p,"w",encoding="utf-8").write(json.dumps(j,indent=2,ensure_ascii=False)+"\n")
PY

set +e
./scripts/guarded_apply.sh -- bash "$DIR/apply.sh" | tee "$LOG"
RC="${PIPESTATUS[0]}"
set -e

# mark final status
python3 - <<PY
import json, datetime
p="$DIR/status.json"
j=json.load(open(p,"r",encoding="utf-8"))
j["apply_finished_at"]=datetime.datetime.now().isoformat(timespec="seconds")
j["log_file"]="$LOG"
j["rc"]=$RC
j["status"]="applied" if $RC==0 else "failed"
open(p,"w",encoding="utf-8").write(json.dumps(j,indent=2,ensure_ascii=False)+"\n")
PY

if [ "$RC" -ne 0 ]; then
  echo
  echo "FAIL: proposal apply failed (rc=$RC). See log: $LOG"
  exit "$RC"
fi

echo
echo "PASS: proposal applied successfully."
