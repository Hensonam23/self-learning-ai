#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== upgrade_boot_logs_v2 =="

cat > scripts/boot_logs.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

echo "== machinespirit boot runner logs =="

echo
echo "--- user-unit logs (correct on this machine) ---"
journalctl --user-unit=machinespirit-boot-runner.service -n 200 --no-pager || true

echo
echo "--- by SyslogIdentifier (also reliable) ---"
journalctl SYSLOG_IDENTIFIER=machinespirit-boot -n 200 --no-pager || true

echo
echo "--- fallback grep (last resort) ---"
journalctl -n 500 --no-pager | grep -i 'machinespirit-boot' | tail -n 200 || true
SH

chmod +x scripts/boot_logs.sh

echo "OK: wrote scripts/boot_logs.sh (v2)"
echo "OK: upgrade_boot_logs_v2 complete"
