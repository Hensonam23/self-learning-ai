# MachineSpirit Setup (LAN API + LAN UI)

## Ports
- API: 8010
- UI: 8020

## Quick Start (no systemd)
1) Install deps into a venv:
   ./scripts/bootstrap_venv.sh

2) Run both services (dev mode):
   ./scripts/run_dev.sh

3) Open UI in browser:
   http://<pi-ip>:8020/ui

Health checks:
- curl -s http://127.0.0.1:8010/health ; echo
- curl -s http://127.0.0.1:8020/health ; echo

## Install as systemd user services (auto-run)
1) Install/enable:
   ./scripts/install_user_services.sh

2) Status:
   systemctl --user status machinespirit-api.service --no-pager
   systemctl --user status machinespirit-ui.service --no-pager

3) Logs:
   journalctl --user -u machinespirit-api.service -n 120 --no-pager
   journalctl --user -u machinespirit-ui.service -n 120 --no-pager

Uninstall:
- ./scripts/uninstall_user_services.sh

## Notes
- By default, the services bind on 0.0.0.0 so they are reachable on your LAN.
- If you want to lock it down later, bind to 127.0.0.1 and proxy it.
