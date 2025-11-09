#!/usr/bin/env bash
set -euo pipefail

ENV_DIR="/home/aaron/self-learning-ai"

# Read token/port from .env (fallback port 8089)
TOKEN="$(awk -F= '/^MS_HTTP_TOKEN=/{print $2}' "$ENV_DIR/.env" | tr -d '\r\n')"
PORT="$(awk -F= '/^HTTP_PORT=/{print $2}' "$ENV_DIR/.env" | tr -d '\r\n')"
PORT="${PORT:-8089}"
HOST="http://localhost:${PORT}"

cmd="${1:-}"; shift || true

case "$cmd" in
  hello)
    curl -s -H "X-MS-Token: $TOKEN" "$HOST/hello"
    ;;
  say)
    text="${*:-}"
    curl -s -G -H "X-MS-Token: $TOKEN" --data-urlencode "text=$text" "$HOST/say"
    ;;
  learn)
    topic="${*:-}"
    curl -s -G -H "X-MS-Token: $TOKEN" --data-urlencode "topic=$topic" "$HOST/learn"
    ;;
  search)
    q="${*:-}"
    curl -s -G -H "X-MS-Token: $TOKEN" --data-urlencode "q=$q" "$HOST/search"
    ;;
  *)
    echo "Usage: ms.sh {hello | say <text> | learn <topic> | search <query>}"
    exit 2
    ;;
esac
echo
