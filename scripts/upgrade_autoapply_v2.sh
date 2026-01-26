#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_autoapply_v2 =="

# ----------------------------
# 1) Patch autoapply_runner: add a real "single-run" lock (flock)
# ----------------------------
cp -a scripts/autoapply_runner.sh "scripts/autoapply_runner.sh.bak.$(date +%Y%m%d-%H%M%S)" || true

if ! grep -q 'MS_AUTOAPPLY_FLOCK_V1' scripts/autoapply_runner.sh; then
  python3 - <<'PY'
from pathlib import Path
p = Path("scripts/autoapply_runner.sh")
txt = p.read_text(encoding="utf-8", errors="replace").splitlines(True)

# Insert right after the "repo:" line (best stable anchor)
out = []
inserted = False
for line in txt:
    out.append(line)
    if (not inserted) and line.strip().startswith('echo "repo:'):
        out.append("\n")
        out.append("# --- MS_AUTOAPPLY_FLOCK_V1 ---\n")
        out.append('mkdir -p data/runtime\n')
        out.append('RUNLOCK="data/runtime/autoapply.flock"\n')
        out.append('exec 9>"$RUNLOCK"\n')
        out.append('if ! flock -n 9; then\n')
        out.append('  echo "AUTOAPPLY: another run is already active -> exiting"\n')
        out.append('  exit 0\n')
        out.append('fi\n')
        out.append("\n")
        inserted = True

p.write_text("".join(out), encoding="utf-8")
print("OK: added flock single-run lock to autoapply_runner.sh")
PY
else
  echo "OK: autoapply_runner.sh already has flock lock"
fi

chmod +x scripts/autoapply_runner.sh

# ----------------------------
# 2) Update systemd unit: longer timeout, no recursion changes needed here
# ----------------------------
cat > systemd/machinespirit-autoapply.service <<'UNIT'
[Unit]
Description=MachineSpirit Auto-Apply (apply latest proposal + selftest + auto-commit)
After=network-online.target machinespirit-api.service machinespirit-ui.service
Wants=network-online.target machinespirit-api.service machinespirit-ui.service

[Service]
Type=oneshot
WorkingDirectory=%h/self-learning-ai
Environment=PYTHONUNBUFFERED=1
Environment=MS_AUTOPUSH=0
SyslogIdentifier=machinespirit-autoapply
StandardOutput=journal
StandardError=journal

# upgrades can take time; avoid premature timeouts
TimeoutStartSec=3600

ExecStart=/bin/bash -lc 'cd %h/self-learning-ai && ./scripts/autoapply_runner.sh'
UNIT

# Timer jitter avoids collisions on boot
cat > systemd/machinespirit-autoapply.timer <<'TIMER'
[Unit]
Description=Run MachineSpirit auto-apply periodically

[Timer]
OnUnitActiveSec=20min
Persistent=true
RandomizedDelaySec=90
AccuracySec=1min

[Install]
WantedBy=timers.target
TIMER

# ----------------------------
# 3) Install to user systemd (source of truth at runtime)
# ----------------------------
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
install -m 0644 systemd/machinespirit-autoapply.service "$UNIT_DIR/machinespirit-autoapply.service"
install -m 0644 systemd/machinespirit-autoapply.timer   "$UNIT_DIR/machinespirit-autoapply.timer"

systemctl --user daemon-reload
systemctl --user enable machinespirit-autoapply.timer >/dev/null 2>&1 || true
systemctl --user restart machinespirit-autoapply.timer >/dev/null 2>&1 || true

# IMPORTANT: do NOT start the service in blocking mode from inside upgrades.
# If you want to kick it once, do it non-blocking.
systemctl --user start machinespirit-autoapply.service --no-block >/dev/null 2>&1 || true

systemctl --user status machinespirit-autoapply.timer --no-pager -l || true
echo "OK: upgrade_autoapply_v2 complete"
