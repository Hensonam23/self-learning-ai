#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_autoapply_autopush_v1 =="

# Update the repo copy
if [ -f systemd/machinespirit-autoapply.service ]; then
  cp -a systemd/machinespirit-autoapply.service "systemd/machinespirit-autoapply.service.bak.$(date +%Y%m%d-%H%M%S)"
fi

# Ensure Environment=MS_AUTOPUSH=1 in unit file
python3 - <<'PY'
from pathlib import Path
import re

p = Path("systemd/machinespirit-autoapply.service")
if not p.exists():
    raise SystemExit("ERROR: missing systemd/machinespirit-autoapply.service in repo")

txt = p.read_text(encoding="utf-8", errors="replace").splitlines(True)

out=[]
seen=False
for line in txt:
    if re.match(r'^\s*Environment\s*=\s*MS_AUTOPUSH\s*=', line):
        out.append("Environment=MS_AUTOPUSH=1\n")
        seen=True
    else:
        out.append(line)

if not seen:
    # insert near other Environment lines in [Service]
    inserted=False
    for i,l in enumerate(out):
        if l.strip()=="[Service]":
            out.insert(i+1, "Environment=MS_AUTOPUSH=1\n")
            inserted=True
            break
    if not inserted:
        out.append("\nEnvironment=MS_AUTOPUSH=1\n")

p.write_text("".join(out), encoding="utf-8")
print("OK: set MS_AUTOPUSH=1 in systemd/machinespirit-autoapply.service")
PY

# Install to user systemd (runtime source of truth)
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
install -m 0644 systemd/machinespirit-autoapply.service "$UNIT_DIR/machinespirit-autoapply.service"

systemctl --user daemon-reload
systemctl --user restart machinespirit-autoapply.timer >/dev/null 2>&1 || true

# kick once, non-blocking
systemctl --user start machinespirit-autoapply.service --no-block >/dev/null 2>&1 || true

systemctl --user status machinespirit-autoapply.timer --no-pager -l || true
echo "OK: upgrade_autoapply_autopush_v1 complete"
