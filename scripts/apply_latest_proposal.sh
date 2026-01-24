#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

if [ ! -d proposals ]; then
  echo "No proposals/ directory."
  exit 0
fi

pick=""

# sort by name desc (timestamp folders sort correctly)
while IFS= read -r name; do
  [[ "$name" == _* ]] && continue
  P="proposals/$name"
  [ -d "$P" ] || continue

  # MUST have a status file to be considered pending
  if [ ! -f "$P/status.txt" ]; then
    continue
  fi

  st="$(tr -d '\r\n' < "$P/status.txt" | tr '[:upper:]' '[:lower:]')"
  [ -n "$st" ] || st="unknown"

  if [ "$st" = "pending" ]; then
    pick="$P"
    break
  fi
done < <(find proposals -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)

if [ -z "$pick" ]; then
  echo "No pending proposals."
  exit 0
fi

./scripts/apply_proposal.sh "$pick"
