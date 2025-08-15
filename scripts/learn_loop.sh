#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/self-learning-ai"
ENV_FILE="$ROOT/.env"
TOPICS="$ROOT/learn_topics.txt"
HOST="http://localhost:8089"

if [[ ! -f "$ENV_FILE" ]]; then echo "Missing $ENV_FILE"; exit 1; fi
TOKEN="$(awk -F= '/^MS_HTTP_TOKEN=/{print $2}' "$ENV_FILE" | tr -d '\r\n')"

touch "$TOPICS"
echo "[learner] starting with token len: ${#TOKEN}"

learn_once () {
  local q="$1"
  [[ -z "$q" ]] && return 0
  echo "[learner] learning: $q"
  curl -s -G -H "X-MS-Token: $TOKEN" --data-urlencode "topic=$q" "$HOST/learn" >/dev/null || true
  # optional: also hit /search for broader crawl
  curl -s -G -H "X-MS-Token: $TOKEN" --data-urlencode "q=$q" "$HOST/search" >/dev/null || true
}

while true; do
  # shuffle topics each pass to vary order a bit
  mapfile -t lines < <(grep -v '^\s*$' "$TOPICS" | sed 's/\r$//' | sort -R)
  if [[ "${#lines[@]}" -eq 0 ]]; then
    echo "[learner] no topics in $TOPICS; sleeping 5m"
    sleep 300
    continue
  fi

  for t in "${lines[@]}"; do
    learn_once "$t"
    # sleep 1–3 minutes between topics to be polite to APIs/sites
    S=$(( 60 + (RANDOM % 120) ))
    echo "[learner] sleeping ${S}s"
    sleep "$S"
  done

  # pause 5–10 minutes before next full pass
  P=$(( 300 + (RANDOM % 300) ))
  echo "[learner] pass complete, sleeping ${P}s"
  sleep "$P"
done
