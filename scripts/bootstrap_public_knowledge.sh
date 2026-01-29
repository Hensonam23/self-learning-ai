#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="knowledge/public_local_knowledge.json"
DST="data/local_knowledge.json"

if [ ! -f "$SRC" ]; then
  echo "ERROR: missing $SRC (did you pull the repo?)"
  exit 1
fi

mkdir -p data

# Default behavior: do NOT overwrite if the target already exists and has content.
if [ -s "$DST" ]; then
  echo "OK: $DST already exists (not overwriting)."
  echo "Tip: FORCE=1 ./scripts/bootstrap_public_knowledge.sh  (to overwrite)"
  exit 0
fi

cp -a "$SRC" "$DST"
echo "OK: bootstrapped $DST from $SRC"
