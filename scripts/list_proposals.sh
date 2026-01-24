#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

echo "== Proposals =="

if [ ! -d proposals ]; then
  echo "(none)"
  exit 0
fi

# list only top-level proposal dirs (ignore underscore dirs like _archive)
for d in $(find proposals -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r); do
  [[ "$d" == _* ]] && continue
  P="proposals/$d"
  st="unknown"
  if [ -f "$P/status.txt" ]; then
    st="$(tr -d '\r\n' < "$P/status.txt" | tr '[:upper:]' '[:lower:]')"
    [ -n "$st" ] || st="unknown"
  fi
  echo "$d  [$st]"
done
