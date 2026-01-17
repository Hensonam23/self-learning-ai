# MachineSpirit Setup (LAN API + LAN UI)

This repo runs two services:

- API (ms_api.py) on port **8010**
- UI  (ms_ui.py) on port **8020**

The UI talks to the API on the same Pi, and you access the UI from any device on your LAN.

---

## Option A (recommended): Run as systemd user services

### Install + start
```bash
cd ~/self-learning-ai
./scripts/install_user_services.sh
