#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Keep last 14 days of nightly logs + snapshots
DAYS=14

find exports -type f -name 'nightly_push_*.log' -mtime +$DAYS -delete || true
find exports -type f -name 'public_local_knowledge.*.json' -mtime +$DAYS -delete || true

# keep the symlink and lock file
echo "OK: cleaned exports/ older than ${DAYS} days"
