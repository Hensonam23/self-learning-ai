#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Use venv python if it exists, else fallback to system python
PY="./venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python3"
fi

mkdir -p logs

echo "Starting Machine Spirit (brain)..." | tee -a logs/startup.log
$PY brain.py
