#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_autoapply_human_guard_v1 =="

cp -a scripts/autoapply_runner.sh "scripts/autoapply_runner.sh.bak.$(date +%Y%m%d-%H%M%S)" || true

if ! grep -q 'MS_HUMAN_GUARD_V1' scripts/autoapply_runner.sh; then
  python3 - <<'PY'
from pathlib import Path
p = Path("scripts/autoapply_runner.sh")
lines = p.read_text(encoding="utf-8", errors="replace").splitlines(True)

out=[]
inserted=False
for line in lines:
    out.append(line)
    if (not inserted) and line.strip().startswith('echo "repo:'):
        out.append("\n")
        out.append("# --- MS_HUMAN_GUARD_V1 ---\n")
        out.append('mkdir -p data/runtime\n')
        out.append('if [ -f "data/runtime/human_busy.flag" ]; then\n')
        out.append('  echo "AUTOAPPLY: human_busy.flag present -> exiting"\n')
        out.append('  exit 0\n')
        out.append('fi\n')
        out.append("\n")
        inserted=True

p.write_text("".join(out), encoding="utf-8")
print("OK: added human busy guard to autoapply_runner.sh")
PY
else
  echo "OK: autoapply_runner.sh already has MS_HUMAN_GUARD_V1"
fi

chmod +x scripts/autoapply_runner.sh
echo "OK: upgrade_autoapply_human_guard_v1 complete"
