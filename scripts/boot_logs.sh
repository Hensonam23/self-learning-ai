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
