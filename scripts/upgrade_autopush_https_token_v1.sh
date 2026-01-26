#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_autopush_https_token_v1 =="

ENV_FILE="$HOME/.config/machinespirit/github.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: missing $ENV_FILE"
  echo "Create it with:"
  echo "  GITHUB_USER=Hensonam23"
  echo "  GITHUB_TOKEN=PASTE_TOKEN_HERE"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [ -z "${GITHUB_USER:-}" ] || [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "ERROR: GITHUB_USER or GITHUB_TOKEN missing in $ENV_FILE"
  exit 1
fi

# Ensure origin is HTTPS
HTTPS_URL="https://github.com/${GITHUB_USER}/self-learning-ai.git"
git remote set-url origin "$HTTPS_URL"
echo "OK: origin set to $HTTPS_URL"

# Use a dedicated credentials file so systemd can push without prompts
CRED_DIR="$HOME/.config/machinespirit"
CRED_FILE="$CRED_DIR/git-credentials"
mkdir -p "$CRED_DIR"
chmod 700 "$CRED_DIR"

# Store token for github.com
# Format: https://USER:TOKEN@github.com
printf "https://%s:%s@github.com\n" "$GITHUB_USER" "$GITHUB_TOKEN" > "$CRED_FILE"
chmod 600 "$CRED_FILE"

git config --global credential.helper "store --file $CRED_FILE"
git config --global push.default simple

echo "OK: git credential helper configured for background pushes"

# quick non-interactive test (no push if clean)
git status --porcelain >/dev/null 2>&1 || true
echo "OK: upgrade_autopush_https_token_v1 complete"
