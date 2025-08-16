#!/usr/bin/env bash
set -euo pipefail
cd /home/aaron/self-learning-ai

# Load .env if present (token, paths, etc.)
[ -f .env ] && set -a && . ./.env && set +a

# Use your desktop session’s runtime; these are safe defaults on your user
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"

# Prefer Wayland; flip to x11 if your session is X11
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-wayland}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

# Audio via Pulse
export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-pulse}"

# If Wayland fails, uncomment next 2 lines and comment the 2 Wayland lines above:
# export SDL_VIDEODRIVER=x11
# export DISPLAY=${DISPLAY:-:0}

exec /home/aaron/self-learning-ai/venv/bin/python3 -u /home/aaron/self-learning-ai/evolve_ai.py
