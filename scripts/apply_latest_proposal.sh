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
while IFS= read -r d; do
  [ -d "$d" ] || continue
  st="pending"
  if [ -f "$d/status.txt" ]; then
    st="$(tr -d '\r\n' < "$d/status.txt" | tr '[:upper:]' '[:lower:]')"
    [ -n "$st" ] || st="pending"
  fi
  if [ "$st" = "pending" ]; then
    pick="$d"
    break
  fi
done < <(ls -1dt proposals/* 2>/dev/null)

if [ -z "$pick" ]; then
  echo "No pending proposals."
  exit 0
fi

./scripts/apply_proposal.sh "$pick"
