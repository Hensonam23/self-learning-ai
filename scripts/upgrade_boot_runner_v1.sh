#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

echo "== upgrade_boot_runner_v1 =="
echo "repo: $REPO_DIR"

mkdir -p scripts systemd

# ----------------------------
# scripts/boot_runner.sh
# ----------------------------
cat > scripts/boot_runner.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== MachineSpirit Boot Runner =="
echo "when: $(date -Is)"
echo "repo: $(pwd)"

# If an upgrade is in progress, don't interfere.
LOCK_FILE="data/runtime/maintenance.lock"
if [ -f "$LOCK_FILE" ]; then
  now=$(date +%s)
  ts=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
  age=$(( now - ts ))
  if [ "$age" -lt 3600 ]; then
    echo "BOOT: maintenance lock present (age=${age}s) -> will NOT apply proposals"
    APPLY_OK="no"
  else
    echo "BOOT: stale maintenance lock (age=${age}s) -> removing"
    rm -f "$LOCK_FILE" || true
    APPLY_OK="yes"
  fi
else
  APPLY_OK="yes"
fi

echo
echo "== boot: selftest (pre) =="
./scripts/selftest.sh

echo
echo "== boot: reflection =="
/usr/bin/python3 scripts/reflect.py || true

echo
echo "== boot: auto_propose =="
/usr/bin/python3 scripts/auto_propose.py || true

echo
echo "== boot: apply 1 pending proposal (optional) =="
if [ "$APPLY_OK" = "yes" ]; then
  ./scripts/apply_latest_proposal.sh || true
else
  echo "BOOT: skipping apply_latest_proposal.sh due to maintenance lock"
fi

echo
echo "== boot: selftest (post) =="
./scripts/selftest.sh

echo
echo "== boot: done =="
echo "when: $(date -Is)"
SH

chmod +x scripts/boot_runner.sh

# ----------------------------
# systemd/machinespirit-boot-runner.service
# ----------------------------
cat > systemd/machinespirit-boot-runner.service <<'UNIT'
[Unit]
Description=MachineSpirit Boot Runner (selftest + reflect + propose + apply)
After=network-online.target machinespirit-api.service machinespirit-ui.service machinespirit-brain.service
Wants=network-online.target machinespirit-api.service machinespirit-ui.service machinespirit-brain.service

[Service]
Type=oneshot
WorkingDirectory=%h/self-learning-ai
Environment=PYTHONUNBUFFERED=1
SyslogIdentifier=machinespirit-boot
StandardOutput=journal
StandardError=journal

# Give it time to run selftest + proposal apply safely
TimeoutStartSec=600

ExecStart=/bin/bash -lc 'cd %h/self-learning-ai && ./scripts/boot_runner.sh'

[Install]
WantedBy=default.target
UNIT

# Install to user systemd (source-of-truth in repo stays in systemd/)
mkdir -p "$HOME/.config/systemd/user"
cp -a systemd/machinespirit-boot-runner.service "$HOME/.config/systemd/user/machinespirit-boot-runner.service"

systemctl --user daemon-reload
systemctl --user enable machinespirit-boot-runner.service

# Run once now so we can verify logs/output immediately
systemctl --user start machinespirit-boot-runner.service

echo
echo "== status =="
systemctl --user status machinespirit-boot-runner.service --no-pager -l || true

echo
echo "== logs (user journal) =="
journalctl --user -u machinespirit-boot-runner.service -n 120 --no-pager || true

echo "OK: upgrade_boot_runner_v1 complete"
