#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[bootstrap] creating venv..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[bootstrap] upgrading pip..."
python -m pip install --upgrade pip >/dev/null

if [ ! -f "requirements.txt" ]; then
  echo "ERROR: requirements.txt not found in $REPO_DIR"
  exit 1
fi

echo "[bootstrap] installing requirements..."
pip install -r requirements.txt

echo
echo "OK: venv ready."
echo "Activate later with:"
echo "  source $REPO_DIR/.venv/bin/activate"
