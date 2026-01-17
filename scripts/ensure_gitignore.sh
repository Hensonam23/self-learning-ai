#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

touch .gitignore

add_line() {
  local line="$1"
  if ! grep -qxF "$line" .gitignore; then
    echo "$line" >> .gitignore
  fi
}

# venv + python noise
add_line ".venv/"
add_line "__pycache__/"
add_line "*.pyc"

# runtime data (safe defaults)
add_line "data/"
add_line "data/api_key.txt"
add_line "data/api_key_header.txt"

echo "OK: .gitignore updated (only appended missing lines)."
