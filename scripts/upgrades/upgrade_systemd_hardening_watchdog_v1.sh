#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
BK="data/backups/upgrade_systemd_hardening_watchdog_v1/$STAMP"
mkdir -p "$BK"

echo "== upgrade_systemd_hardening_watchdog_v1 =="
echo "repo:   $REPO_DIR"
echo "backup: $BK"

# backups (repo files only)
for f in scripts/auto_propose.py systemd/; do
  if [ -e "$f" ]; then
    mkdir -p "$BK/$(dirname "$f")"
    cp -a "$f" "$BK/$f" || true
  fi
done

mkdir -p scripts systemd

# ------------------------------------------------------------
# 1) Watchdog script (health check + restart + optional selftest)
# ------------------------------------------------------------
cat > scripts/watchdog_check.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

MS_API_KEY="$(grep -m1 '^MS_API_KEY=' ~/.config/machinespirit/secrets.env | cut -d= -f2- || true)"

check_api () {
  curl -fsS -m 2 http://127.0.0.1:8010/health -H "X-API-Key: $MS_API_KEY" >/dev/null
}
check_ui () {
  curl -fsS -m 2 http://127.0.0.1:8020/health >/dev/null
}

ok=1

if ! check_api; then
  echo "WATCHDOG: API health failed -> restarting machinespirit-api.service"
  systemctl --user restart machinespirit-api.service || true
  sleep 2
  check_api || ok=0
fi

if ! check_ui; then
  echo "WATCHDOG: UI health failed -> restarting machinespirit-ui.service"
  systemctl --user restart machinespirit-ui.service || true
  sleep 2
  check_ui || ok=0
fi

if [ "$ok" -ne 1 ]; then
  echo "WATCHDOG: health still failing -> running selftest for debug"
  ./scripts/selftest.sh || true
  exit 1
fi

echo "WATCHDOG: ok"
SH
chmod +x scripts/watchdog_check.sh

# ------------------------------------------------------------
# 2) Watchdog systemd user unit + timer
# ------------------------------------------------------------
cat > systemd/machinespirit-watchdog.service <<'UNIT'
[Unit]
Description=MachineSpirit watchdog (health check + self-heal)
After=machinespirit-api.service machinespirit-ui.service

[Service]
Type=oneshot
ExecStart=/bin/bash -lc 'cd %h/self-learning-ai && ./scripts/watchdog_check.sh'
UNIT

cat > systemd/machinespirit-watchdog.timer <<'UNIT'
[Unit]
Description=Run MachineSpirit watchdog every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# install watchdog units into user systemd
mkdir -p ~/.config/systemd/user
cp -a systemd/machinespirit-watchdog.service ~/.config/systemd/user/
cp -a systemd/machinespirit-watchdog.timer   ~/.config/systemd/user/

# ------------------------------------------------------------
# 3) Hardening drop-ins for API + UI (run forever)
# ------------------------------------------------------------
mkdir -p ~/.config/systemd/user/machinespirit-api.service.d
mkdir -p ~/.config/systemd/user/machinespirit-ui.service.d

cat > ~/.config/systemd/user/machinespirit-api.service.d/hardening.conf <<'DROPIN'
[Unit]
StartLimitIntervalSec=0
StartLimitBurst=0

[Service]
WorkingDirectory=%h/self-learning-ai
Restart=always
RestartSec=2
TimeoutStopSec=20
KillSignal=SIGINT
DROPIN

cat > ~/.config/systemd/user/machinespirit-ui.service.d/hardening.conf <<'DROPIN'
[Unit]
StartLimitIntervalSec=0
StartLimitBurst=0

[Service]
WorkingDirectory=%h/self-learning-ai
Restart=always
RestartSec=2
TimeoutStopSec=20
KillSignal=SIGINT
DROPIN

# ------------------------------------------------------------
# 4) Fix auto_propose SyntaxError by making it call new_proposal safely
# ------------------------------------------------------------
cat > scripts/auto_propose.py <<'PY'
#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
PROPOSALS_DIR = REPO_DIR / "proposals"
DATA_DIR = REPO_DIR / "data"
QUEUE_PATH = DATA_DIR / "research_queue.json"

def status_of(p: Path) -> str:
    st = (p / "status.txt")
    if st.exists():
        s = st.read_text(encoding="utf-8", errors="replace").strip().lower()
        return s or "pending"
    return "unknown"

def any_pending() -> bool:
    if not PROPOSALS_DIR.exists():
        return False
    for d in PROPOSALS_DIR.iterdir():
        if d.is_dir() and not d.name.startswith("_"):
            if status_of(d) == "pending":
                return True
    return False

def pending_research_count() -> int:
    try:
        if not QUEUE_PATH.exists():
            return 0
        q = json.loads(QUEUE_PATH.read_text(encoding="utf-8", errors="replace") or "[]")
        if not isinstance(q, list):
            return 0
        return sum(1 for i in q if isinstance(i, dict) and i.get("status") == "pending")
    except Exception:
        return 0

def main() -> int:
    os.chdir(str(REPO_DIR))

    # don't stack proposals
    if any_pending():
        print("INFO: pending proposal already exists; skipping auto-propose.")
        return 0

    pending = pending_research_count()

    # If nothing pending, do nothing (quietly)
    if pending <= 0:
        print("INFO: no pending research; skipping auto-propose.")
        return 0

    # Create a simple maintenance proposal: run curiosity n=5
    title = f"Maintenance: curiosity n=5 (pending research: {pending})"
    cmd = "cd ~/self-learning-ai && /usr/bin/python3 brain.py --curiosity --n 5"

    print(f"INFO: creating proposal: {title}")

    # Use new_proposal.sh so proposal format stays consistent and quoting is safe
    subprocess.run(
        ["bash", "-lc", f'./scripts/new_proposal.sh "{title}" --shell {json.dumps(cmd)}'],
        check=True,
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
PY
chmod +x scripts/auto_propose.py

# ------------------------------------------------------------
# 5) Reload systemd user + enable watchdog + restart services
# ------------------------------------------------------------
systemctl --user daemon-reload
systemctl --user enable --now machinespirit-watchdog.timer
systemctl --user restart machinespirit-api.service machinespirit-ui.service

echo "OK: systemd hardening + watchdog enabled"
echo "Check timers:"
systemctl --user list-timers --all | grep -E 'machinespirit-(watchdog|reflect|autopropose|webqueue|curiosity)' || true
