# MachineSpirit - Current Status (Source of Truth)

Last updated: 2026-01-26

## Project rule (do not break)
Portable-first. Everything should be designed to run on:
- Raspberry Pi (Linux/ARM)
- Windows
- Linux (x86_64)
- macOS
- iOS later (and Android optional later)

Web UI stays the universal UI. Desktop/mobile apps can be added later as wrappers or native clients, but they must use the same API contract.

## What is running right now (working)
### Services (systemd --user)
- machinespirit-brain.service (headless brain loop)
- machinespirit-api.service (FastAPI/Uvicorn API)
- machinespirit-ui.service (FastAPI/Uvicorn UI + proxy)

### Ports / exposure model
- API: 127.0.0.1:8010 (localhost only)
- UI:  127.0.0.1:8020 (localhost only)
- Caddy: :80 and :443 (LAN entry point)

Caddy reverse-proxies to the localhost UI on 8020.
Caddy provides HTTPS (`tls internal`) and redirects HTTP->HTTPS.

### Admin-only vs normal user
Normal users:
- /ui (web page)
- /api/ask
- GET /api/theme

Admin-only (enforced at Caddy):
- POST /api/theme
- /api/override

Goal: anyone can chat, but only admin can change knowledge/settings.

## Caddy config (current)
Location:
- /etc/caddy/Caddyfile

Current behavior:
- http://10.0.0.4 -> redirects to https://10.0.0.4
- https://10.0.0.4 -> proxies to 127.0.0.1:8020
- Basic auth required for /api/override and POST /api/theme

## Key files / locations
Repo:
- ~/self-learning-ai

UI app:
- ms_ui.py

API app:
- ms_api.py

Brain:
- brain.py

Knowledge store:
- data/local_knowledge.json

Logs (file-based right now):
- data/logs/brain.log
- data/logs/api.log
- data/logs/ui.log
- data/logs/webqueue.service.log
- data/logs/curiosity.service.log
- etc...

Note: systemd journal may show "No entries" because output is being appended to log files via StandardOutput=append.

## Systemd unit drop-ins (important)
UI binds localhost:
- ~/.config/systemd/user/machinespirit-ui.service.d/bind-localhost.conf

API binds localhost:
- ~/.config/systemd/user/machinespirit-api.service.d/bind-localhost.conf

UI/API log to files:
- ~/.config/systemd/user/machinespirit-ui.service.d/logging.conf
- ~/.config/systemd/user/machinespirit-api.service.d/logging.conf

Curiosity/webqueue use venv python:
- ~/.config/systemd/user/machinespirit-curiosity.service.d/venv.conf
- ~/.config/systemd/user/machinespirit-webqueue.service.d/venv.conf
(and weekly curiosity too)

## “Do NOT resurrect” list
- Do not re-enable old legacy `machine-spirit*` units (they were quarantined).
- Do not recreate old broken `machinespirit.service`.
- Keep API/UI on localhost only. Caddy is the only public entry point.

## Quick restart commands
User services:
- systemctl --user restart machinespirit-brain.service
- systemctl --user restart machinespirit-api.service
- systemctl --user restart machinespirit-ui.service

Caddy (system service):
- sudo systemctl restart caddy

## Smoke test
Run:
- scripts/smoke_test.sh

Expected:
- /ui returns 200
- /api/ask returns ok json
- /api/override returns 401 without admin creds
- HTTPS works

## Roadmap (high level)
Backups are deferred until the 1TB SSD is connected.

Next work order:
1) Docs + smoke tests (freeze current state)
2) Admin login inside UI (no browser popups), role-based UI buttons
3) CSRF protection for admin actions
4) Rate limiting + payload limits
5) Audit log for admin changes
6) Learning quality upgrades (normalize topics, synthesize, confidence + why trace)
7) UI control panel pages
8) PC Node + desktop apps later (PC compute sends suggestions back to Pi; Pi stays source of truth)
