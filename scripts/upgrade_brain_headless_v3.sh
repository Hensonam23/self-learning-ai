#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
echo "== upgrade_brain_headless_v3 =="
echo "repo: $(pwd)"

cp -a brain.py "brain.py.bak.$(date +%Y%m%d-%H%M%S)"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("brain.py")
txt = p.read_text(encoding="utf-8", errors="replace")

MARK = "MS_HEADLESS_V3"
if MARK in txt:
    print("OK: already patched (MS_HEADLESS_V3)")
    raise SystemExit(0)

m = re.search(r'(?m)^if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:\s*$', txt)
insert_at = m.start() if m else len(txt)

block_lines = [
"",
"# ============================================================",
"# MS_HEADLESS_V3",
"# - Honor --help and --headless BEFORE interactive loop starts",
"# - Safe for systemd (no stdin required)",
"# ============================================================",
"def _ms_print_help_v3():",
"    print('''MachineSpirit brain.py\\n\\nUsage:\\n  python3 brain.py\\n      Interactive mode\\n\\n  python3 brain.py --headless\\n      Headless mode (systemd): stays running + prints heartbeat\\n\\n  python3 brain.py --help\\n      Show this help\\n''')",
"",
"def _ms_headless_loop_v3():",
"    import time, signal, datetime",
"    stop = {'flag': False}",
"    def _h(sig, frame):",
"        stop['flag'] = True",
"    for s in (signal.SIGTERM, signal.SIGINT):",
"        try:",
"            signal.signal(s, _h)",
"        except Exception:",
"            pass",
"    print('Machine Spirit headless loop online.')",
"    last = 0.0",
"    while not stop['flag']:",
"        now = time.time()",
"        if now - last >= 60.0:",
"            ts = datetime.datetime.now().isoformat(timespec='seconds')",
"            print(f'[headless] heartbeat {ts}')",
"            last = now",
"        time.sleep(1.0)",
"    print('Machine Spirit headless loop shutting down.')",
"    return 0",
"",
"if __name__ == '__main__':",
"    import sys",
"    if '--help' in sys.argv or '-h' in sys.argv:",
"        _ms_print_help_v3()",
"        raise SystemExit(0)",
"    if '--headless' in sys.argv:",
"        raise SystemExit(_ms_headless_loop_v3())",
f"    # {MARK}",
"",
]

block = "\n".join(block_lines)

# Insert block right BEFORE existing __main__ (if found), otherwise append
new_txt = txt[:insert_at] + block + txt[insert_at:]
p.write_text(new_txt, encoding="utf-8")
print("OK: patched brain.py with MS_HEADLESS_V3")
PY

python3 -m py_compile brain.py
echo "OK: brain.py compiles"

# Update service file (repo + installed)
cat > systemd/machinespirit-brain.service <<'UNIT'
[Unit]
Description=MachineSpirit Brain (headless loop)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/self-learning-ai
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/self-learning-ai/.venv/bin/python %h/self-learning-ai/brain.py --headless
Restart=always
RestartSec=2
SyslogIdentifier=machinespirit-brain
StandardOutput=journal
StandardError=journal
Nice=5

[Install]
WantedBy=default.target
UNIT

mkdir -p "$HOME/.config/systemd/user"
cp -a systemd/machinespirit-brain.service "$HOME/.config/systemd/user/machinespirit-brain.service"

systemctl --user daemon-reload
systemctl --user enable machinespirit-brain.service >/dev/null 2>&1 || true
systemctl --user restart machinespirit-brain.service

sleep 1
systemctl --user status machinespirit-brain.service --no-pager -l || true

echo "OK: upgrade_brain_headless_v3 complete"
