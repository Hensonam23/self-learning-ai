#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p exports
ts="$(date +%Y%m%d_%H%M%S)"
log="exports/nightly_push_${ts}.log"

exec > >(tee -a "$log") 2>&1

echo "=== Nightly public knowledge push: $ts ==="

# keep in sync
(git pull --rebase origin main || true)


# Make sure repo is in a clean-ish state before we start
git rev-parse --is-inside-work-tree >/dev/null

# Update public pack from sanitized export (portable outputs remain ignored)
./scripts/update_public_knowledge.sh

# Refuse to proceed if OTHER tracked files changed
changed="$(git status --porcelain --untracked-files=no)"
if [ -n "$changed" ]; then
  # allow ONLY public_local_knowledge.json changes
  bad="$(echo "$changed" | rg -v '^\s*[AM]\s+knowledge/public_local_knowledge\.json$' || true)"
  if [ -n "$bad" ]; then
    echo "REFUSING: unexpected tracked changes present:"
    echo "$bad"
    echo "Keeping local snapshot only. Not committing/pushing."
    exit 2
  fi
fi

# If no diff in the public pack, stop
if git diff --quiet -- knowledge/public_local_knowledge.json; then
  echo "OK: no changes in knowledge/public_local_knowledge.json"
  exit 0
fi

# Red-flag scans (fail closed)
echo "Scanning knowledge/public_local_knowledge.json for red flags..."
rg -n -i 'password|passcode|api key|authorization:\s*\S+|bearer\s+[A-Za-z0-9._-]+|token\s*[:=]\s*\S+|secret\s*[:=]\s*\S+|ssh\s+[^ \n]*@[^ \n]+' \
  knowledge/public_local_knowledge.json && { echo "FAIL: credential-like strings detected"; exit 3; } || true

rg -n -i '[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}' knowledge/public_local_knowledge.json \
  && { echo "FAIL: email detected"; exit 4; } || true

rg -n -e '\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b' \
  knowledge/public_local_knowledge.json && { echo "FAIL: private IP detected"; exit 5; } || true


# Block name-prompt strings from ever entering the public pack
rg -n -i "my name is|what is my name|whats my name|what's my name|do you know my name" \
  knowledge/public_local_knowledge.json && { echo "FAIL: name-prompt string detected"; exit 6; } || true

echo "OK: scans passed."

# Commit + push (main only)
git add knowledge/public_local_knowledge.json
git commit -m "Nightly public knowledge update ($ts)" || true
git push origin main

echo "OK: pushed."
