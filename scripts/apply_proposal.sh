#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if [ $# -ne 1 ]; then
  echo "Usage: ./scripts/apply_proposal.sh <proposal_dir>"
  exit 2
fi

P="$1"
if [ ! -d "$P" ]; then
  echo "ERROR: proposal dir not found: $P"
  exit 2
fi
if [ ! -f "$P/apply.sh" ]; then
  echo "ERROR: apply.sh not found in: $P"
  exit 2
fi

LOG="$P/apply_$(date +%Y%m%d-%H%M%S).log"

echo "== apply_proposal =="
echo "Proposal: $P"
echo "Log: $LOG"
echo

set +e
( cd "$REPO_DIR" && bash "$P/apply.sh" ) 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e

if [ $RC -ne 0 ]; then
  echo "failed" > "$P/status.txt"
  echo
  echo "FAIL: proposal apply failed (rc=$RC)"
  exit $RC
fi

echo "applied" > "$P/status.txt"
echo
echo "PASS: proposal applied successfully."
