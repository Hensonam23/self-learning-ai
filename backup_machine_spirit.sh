#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p backups
mkdir -p logs

TS="$(date +%Y-%m-%d_%H-%M-%S)"
OUT="backups/machine_spirit_backup_${TS}.tgz"

# Stuff we want backed up (even if git ignores it)
# Do NOT include venv or huge folders
INCLUDE=(
  "data/aliases.json"
  "data/local_knowledge.json"
  "data/taught_knowledge.json"
  "data/research_queue.json"
  "data/research_notes.json"
  "data/template_requests.json"
  "data/memory/"
  "logs/"
)

echo "Creating backup: $OUT" | tee -a logs/backup.log

EXISTING=()
for p in "${INCLUDE[@]}"; do
  if [ -e "$p" ]; then
    EXISTING+=("$p")
  fi
done

if [ "${#EXISTING[@]}" -eq 0 ]; then
  echo "Nothing to back up. No expected files found." | tee -a logs/backup.log
  exit 1
fi

tar -czf "$OUT" "${EXISTING[@]}"

echo "Backup complete." | tee -a logs/backup.log
echo "  Saved: $OUT" | tee -a logs/backup.log

# Keep only newest 20 backups
COUNT=$(ls -1 backups/machine_spirit_backup_*.tgz 2>/dev/null | wc -l || true)
if [ "$COUNT" -gt 20 ]; then
  REMOVE=$((COUNT - 20))
  ls -1t backups/machine_spirit_backup_*.tgz | tail -n "$REMOVE" | while read -r f; do
    rm -f "$f"
    echo "Removed old backup: $f" | tee -a logs/backup.log
  done
fi
