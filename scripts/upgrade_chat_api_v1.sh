#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_chat_api_v1 =="

mkdir -p data/chat_sessions

# Patch API server to add /api/chat endpoints (minimal + safe).
# We will insert code only if markers not present.

if ! grep -Rqs "MS_CHAT_API_V1" scripts; then
  echo "WARN: can't auto-detect where your API file lives from here."
  echo "Tell me which file defines /api/ask (example: scripts/ui_server.py or api_server.py) and I’ll give you the exact patch."
  exit 1
fi

echo "OK: upgrade_chat_api_v1 complete (noop - already applied)"
