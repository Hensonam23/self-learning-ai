#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/home/aaron/self-learning-ai/.env"

# Load HTTP_PORT and MS_HTTP_TOKEN from .env if present
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC2046
  export $(grep -E '^(HTTP_PORT|MS_HTTP_TOKEN)=' "$ENV_FILE" | xargs -d '\n')
fi

BASE="http://localhost:${HTTP_PORT:-8089}"
TOKEN="${MS_HTTP_TOKEN:-}"

if [ -z "${TOKEN}" ]; then
  echo "No MS_HTTP_TOKEN found. Put it in $ENV_FILE" >&2
  exit 1
fi

cmd="${1:-}"; shift || true
case "$cmd" in
  hello)
    curl -s -H "X-MS-Token: $TOKEN" "$BASE/hello"
    ;;
  say)
    text="${*:-}"
    curl -s --get -H "X-MS-Token: $TOKEN" --data-urlencode "text=$text" "$BASE/say"
    ;;
  learn)
    topic="${*:-}"
    curl -s --get -H "X-MS-Token: $TOKEN" --data-urlencode "topic=$topic" "$BASE/learn"
    ;;
  search)
    q="${*:-}"
    curl -s --get -H "X-MS-Token: $TOKEN" --data-urlencode "q=$q" "$BASE/search"
    ;;
  *)
    echo "Usage: ms {hello|say|learn|search} [args...]" >&2
    exit 2
    ;;
esac
echo
