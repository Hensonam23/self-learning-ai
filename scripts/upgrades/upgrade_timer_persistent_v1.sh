#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_timer_persistent_v1 =="
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

timers=(
  machinespirit-reflect.timer
  machinespirit-autopropose.timer
  machinespirit-webqueue.timer
  machinespirit-curiosity.timer
  machinespirit-curiosity-weekly.timer
  machinespirit-watchdog.timer
)

patch_one () {
  local name="$1"
  local dst="$UNIT_DIR/$name"

  local src=""
  if [ -f "systemd/$name" ]; then
    src="systemd/$name"
  elif [ -f "$dst" ]; then
    src="$dst"
  else
    echo "WARN: missing $name (skipping)"
    return 0
  fi

  tmp="$(mktemp)"
  cp -a "$src" "$tmp"

  if ! grep -q '^[[:space:]]*Persistent[[:space:]]*=' "$tmp"; then
    awk '
      {print}
      $0 ~ /^\[Timer\]/ && !done {print "Persistent=true"; done=1}
    ' "$tmp" > "$tmp.p" && mv "$tmp.p" "$tmp"
  fi

  install -m 0644 "$tmp" "$dst"
  if [ -f "systemd/$name" ]; then
    install -m 0644 "$tmp" "systemd/$name"
  fi

  rm -f "$tmp"
  echo "OK: patched $name"
}

for t in "${timers[@]}"; do
  patch_one "$t"
done

systemctl --user daemon-reload
for t in "${timers[@]}"; do
  systemctl --user enable "$t" >/dev/null 2>&1 || true
  systemctl --user restart "$t" >/dev/null 2>&1 || true
done

systemctl --user list-timers --all | grep machinespirit || true
echo "OK: upgrade_timer_persistent_v1 complete"
