# MachineSpirit Setup (LAN API + LAN UI)

## What this repo runs
- MachineSpirit API (FastAPI): port 8010
- MachineSpirit UI (FastAPI): port 8020

UI talks to API locally on the Pi.

## Ports
- API: 8010
- UI: 8020

## API key (important)
The API requires an API key header for protected endpoints like `/health`.

Header:
- X-API-Key: <value of MS_API_KEY>

The key is stored here (auto-created by install scripts):
- ~/.config/machinespirit/secrets.env

Example (health check with key):
```bash
MS_API_KEY="$(grep -m1 '^MS_API_KEY=' ~/.config/machinespirit/secrets.env | cut -d= -f2-)"
curl -s http://127.0.0.1:8010/health -H "X-API-Key: $MS_API_KEY" ; echo
