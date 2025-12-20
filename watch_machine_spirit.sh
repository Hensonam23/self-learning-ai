#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

PY="./venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python3"
fi

mkdir -p logs

echo "Watcher online. Starting research loop..." | tee -a logs/watcher.log

while true; do
  echo "---- $(date) ----" | tee -a logs/watcher.log
  $PY research_worker.py | tee -a logs/watcher.log || true
  sleep 300
done
