#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ $# -ne 1 ]; then
  echo "Usage:"
  echo "  ./restore_machine_spirit.sh backups/machine_spirit_backup_YYYY-MM-DD_HH-MM-SS.tgz"
  exit 1
fi

ARCHIVE="$1"

if [ ! -f "$ARCHIVE" ]; then
  echo "Backup file not found: $ARCHIVE"
  exit 1
fi

mkdir -p logs
echo "Restoring from: $ARCHIVE" | tee -a logs/backup.log

tar -xzf "$ARCHIVE"

echo "Restore complete." | tee -a logs/backup.log
echo "Tip: Run python3 status.py to confirm everything looks right."
