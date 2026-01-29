#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p exports
ts="$(date +%Y%m%d_%H%M%S)"
log="exports/nightly_push_${ts}.log"

exec > >(tee -a "$log") 2>&1

echo "=== Nightly public knowledge push: $ts ==="

# Security + reliability defaults
umask 077
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/true

# Prevent overlapping runs
lockfile="exports/nightly_push.lock"
exec 9>"$lockfile"
command -v flock >/dev/null 2>&1 && flock -n 9 || { echo "OK: already running (lock held)."; exit 0; }

# If a rebase is already in progress, fail closed
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "REFUSING: rebase already in progress. Fix it manually before nightly pushes."
  exit 20
fi

# keep in sync
git pull --rebase origin main


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


# Sanity check: public pack must be valid JSON dict, reasonable sizes
python3 - <<'PY'
import json
p="knowledge/public_local_knowledge.json"
obj=json.load(open(p,"r",encoding="utf-8"))
assert isinstance(obj, dict), "public pack must be a dict"
assert len(obj) > 0, "public pack is empty"
for k,v in list(obj.items())[:50]:
    assert isinstance(k, str) and k.strip(), "empty topic key"
    if isinstance(v, dict):
        ans = (v.get("answer") or "")
        if ans and len(ans) > 20000:
            raise AssertionError(f"answer too long for topic: {k}")
print("OK: public pack JSON sanity check passed. topics =", len(obj))
PY

echo "OK: scans passed."

# Commit + push (main only)
git add knowledge/public_local_knowledge.json
git commit -m "Nightly public knowledge update ($ts)" || true
git push origin main

echo "OK: pushed."
