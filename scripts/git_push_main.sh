#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Load token/user/repo from env file (required for autopush)
set -a
source "$HOME/.config/machinespirit/github.env"
set +a

export GIT_TERMINAL_PROMPT=0

ASKPASS="$(mktemp)"
chmod 700 "$ASKPASS"
cat > "$ASKPASS" <<'SH'
#!/usr/bin/env sh
case "$1" in
  *Username*) echo "$MS_GITHUB_USER" ;;
  *Password*) echo "$MS_GITHUB_TOKEN" ;;
  *) echo "" ;;
esac
SH

export GIT_ASKPASS="$ASKPASS"

# Push (origin MUST be https://github.com/...)
git push origin main

rm -f "$ASKPASS"
