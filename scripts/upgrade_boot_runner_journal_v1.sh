#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_boot_runner_journal_v1 =="

UNIT_USER="$HOME/.config/systemd/user/machinespirit-boot-runner.service"
if [ ! -f "$UNIT_USER" ]; then
  echo "ERROR: missing: $UNIT_USER"
  exit 1
fi

cp -a "$UNIT_USER" "$UNIT_USER.bak.$(date +%Y%m%d-%H%M%S)"

python3 - <<'PY'
from pathlib import Path
import re

unit = Path.home() / ".config/systemd/user/machinespirit-boot-runner.service"
txt = unit.read_text(encoding="utf-8", errors="replace")
lines = txt.splitlines(True)

# find [Service]
svc_i = None
for i,l in enumerate(lines):
    if l.strip() == "[Service]":
        svc_i = i
        break
if svc_i is None:
    raise SystemExit("ERROR: [Service] section not found in boot-runner unit")

# end of [Service]
end_i = len(lines)
for j in range(svc_i+1, len(lines)):
    if re.match(r"^\s*\[.+\]\s*$", lines[j]):
        end_i = j
        break

svc = lines[svc_i:end_i]

def has_key(k: str) -> bool:
    return any(re.match(rf"^\s*{re.escape(k)}\s*=", x) for x in svc)

add = []
if not has_key("SyslogIdentifier"):
    add.append("SyslogIdentifier=machinespirit-boot\n")
if not has_key("StandardOutput"):
    add.append("StandardOutput=journal\n")
if not has_key("StandardError"):
    add.append("StandardError=journal\n")
if not has_key("Environment"):
    add.append("Environment=PYTHONUNBUFFERED=1\n")

if add:
    # insert right after [Service]
    svc = [svc[0]] + add + svc[1:]
    lines = lines[:svc_i] + svc + lines[end_i:]
    unit.write_text("".join(lines), encoding="utf-8")
    print("OK: patched boot-runner unit with journald settings")
else:
    print("OK: boot-runner unit already has journald settings")
PY

# helper: clean boot log viewing
cat > scripts/boot_logs.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "== machinespirit boot runner logs =="
echo "--- system journal (always works on this box) ---"
journalctl -u machinespirit-boot-runner.service -n 200 --no-pager || true
echo
echo "--- user journal (may be empty depending on journald config) ---"
journalctl --user -u machinespirit-boot-runner.service -n 200 --no-pager || true
SH
chmod +x scripts/boot_logs.sh

systemctl --user daemon-reload
systemctl --user restart machinespirit-boot-runner.service || true

echo "OK: upgrade_boot_runner_journal_v1 complete"
