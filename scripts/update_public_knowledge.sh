#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p exports

# Build sanitized export (these outputs are ignored by git)
python3 scripts/export_portable_knowledge.py

ts="$(date +%Y%m%d_%H%M%S)"

# Keep local history copies (never committed)
cp -a knowledge/portable_local_knowledge.json "exports/public_local_knowledge.${ts}.json" 2>/dev/null || true
cp -a knowledge/portable_manifest.json       "exports/public_manifest.${ts}.json" 2>/dev/null || true

# Update the tracked public pack
cp -a knowledge/portable_local_knowledge.json knowledge/public_local_knowledge.json

echo "OK: updated knowledge/public_local_knowledge.json (and saved a local snapshot in exports/)"
