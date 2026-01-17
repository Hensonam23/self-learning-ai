#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ms_theme import ThemeConfig, apply_theme, load_theme, save_theme, ui_intensity_choices


APP_NAME = "MachineSpirit API"
VERSION = "0.3.5"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")
MS_API_KEY = os.environ.get("MS_API_KEY", "")

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))


app = FastAPI(title=APP_NAME, version=VERSION)


# ----------------------------
# Models
# ----------------------------
class AskRequest(BaseModel):
    text: str
    timeout_s: Optional[int] = 25
    raw: Optional[bool] = False


class AskResponse(BaseModel):
    ok: bool
    topic: str
    answer: str
    duration_s: float
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    theme: Optional[Dict[str, str]] = None


class ThemeRequest(BaseModel):
    theme: str
    intensity: str


# ----------------------------
# Auth
# ----------------------------
def _require_auth(request: Request) -> None:
    if not MS_API_KEY:
        raise HTTPException(status_code=500, detail="MS_API_KEY is not set on server")
    key = request.headers.get("x-api-key", "")
    if key != MS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ----------------------------
# Brain process helpers
# ----------------------------
def _brain_repl_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]


def _normalize_topic(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", s, flags=re.IGNORECASE).strip()
    return s


def _clean_repl_stdout(raw: str) -> str:
    """
    Turns brain REPL stdout into a clean answer.

    Handles prompt styles:
      - '> CIDR'
      - '>CIDR'
    Stops at:
      - '> Shutting down.'
    """
    if not raw:
        return ""

    prompt_re = re.compile(r"^\s*>\s*(.*)$")
    lines = raw.splitlines()

    topic = ""
    body: List[str] = []
    saw_topic = False

    for line in lines:
        s = line.strip()

        # drop banner
        if s.startswith("Machine Spirit brain online."):
            continue

        pm = prompt_re.match(line)
        if pm:
            prompt_text = (pm.group(1) or "").strip()

            # end-of-session prompt
            if "shutting down" in prompt_text.lower():
                break

            # first prompt is the topic
            if (not saw_topic) and prompt_text:
                topic = prompt_text
                saw_topic = True
                continue

            # any later prompt means we reached the end
            break

        # skip any stray shutdown lines
        if "shutting down" in s.lower():
            continue

        body.append(line.rstrip())

    # trim leading blank lines
    while body and body[0].strip() == "":
        body.pop(0)

    body_text = "\n".join(body).strip()

    if topic and body_text:
        return f"{topic}\n\n{body_text}".strip()
    if topic:
        return topic.strip()
    return body_text.strip()


def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return True
    # classic “junk” style answers
    if "may refer to" in a:
        return True
    if a.endswith("sources:\n-") or a.endswith("sources:\n- "):
        return True
    return False


FALLBACK_MINI: Dict[str, str] = {
    "nat": (
        "NAT (Network Address Translation)\n\n"
        "Definition:\n"
        "- A router feature that translates private IP addresses (like 192.168.x.x) to a public IP address so multiple devices can share one internet connection.\n\n"
        "Key points:\n"
        "- Common in home networks.\n"
        "- Helps conserve IPv4 addresses.\n"
        "- Not a firewall by itself, but it reduces unsolicited inbound connections.\n\n"
        "Sources:\n"
        "- RFC 3022: https://www.rfc-editor.org/rfc/rfc3022\n"
    ),
}


async def _run_brain_repl(topic_line: str, timeout_s: int) -> Dict[str, Any]:
    """
    Runs brain.py as a subprocess, feeds one line, reads stdout/stderr.
    Uses a simple lock file to avoid overlapping calls under load.
    """
    # Lock (best-effort)
    LOCK_PATH.touch(exist_ok=True)

    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        *_brain_repl_args(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_DIR),
    )

    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(input=(topic_line + "\n").encode("utf-8")),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="brain.py timed out")

    dt = time.time() - t0
    stdout = (out_b or b"").decode("utf-8", errors="replace")
    stderr = (err_b or b"").decode("utf-8", errors="replace")
    rc = int(proc.returncode or 0)

    return {
        "exit_code": rc,
        "duration_s": dt,
        "stdout": stdout,
        "stderr": stderr,
        "args": _brain_repl_args(),
    }


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "ok": True,
        "app": APP_NAME,
        "version": VERSION,
        "ui_hint": "Main UI is on port 8020 (MachineSpirit UI).",
    }


@app.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    cfg = load_theme()
    return {
        "ok": True,
        "app": APP_NAME,
        "version": VERSION,
        "brain_path": str(BRAIN_PATH),
        "repo_dir": str(REPO_DIR),
        "python": PYTHON_BIN,
        "lock_path": str(LOCK_PATH),
        "theme": {"theme": cfg.theme, "intensity": cfg.intensity},
    }


@app.get("/theme")
async def get_theme(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    cfg = load_theme()
    choices = ui_intensity_choices()
    return {
        "ok": True,
        "theme": cfg.theme,
        "intensity": cfg.intensity,
        "choices": choices,
    }


@app.post("/theme")
async def set_theme_endpoint(request: Request, payload: ThemeRequest) -> Dict[str, Any]:
    _require_auth(request)
    cfg = save_theme(payload.theme, payload.intensity)
    return {"ok": True, "theme": cfg.theme, "intensity": cfg.intensity}


@app.post("/ask", response_model=AskResponse)
async def ask(request: Request, req: AskRequest) -> AskResponse:
    _require_auth(request)

    t0 = time.time()
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")

    # Allow changing theme by typing /theme into chat/UI
    if text.lower().startswith("/theme"):
        parts = text.split()
        if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() == "status"):
            cfg = load_theme()
            choices = ui_intensity_choices()
            msg = (
                f"Theme is currently: {cfg.theme} ({cfg.intensity})\n\n"
                "Set it like:\n"
                "- /theme off\n"
                "- /theme set Warhammer 40k light\n"
                "- /theme set Warhammer 40k heavy\n\n"
                f"{choices['light']['label']} - {choices['light']['desc']}\n"
                f"{choices['heavy']['label']} - {choices['heavy']['desc']}\n"
            )
            return AskResponse(
                ok=True,
                topic="theme",
                answer=msg.strip(),
                duration_s=time.time() - t0,
                error=None,
                raw=None,
                theme={"theme": cfg.theme, "intensity": cfg.intensity},
            )

        # /theme off
        if len(parts) >= 2 and parts[1].lower() in ("off", "none", "disable", "disabled"):
            cfg = save_theme("none", "light")
            return AskResponse(
                ok=True,
                topic="theme",
                answer="Theme disabled.",
                duration_s=time.time() - t0,
                error=None,
                raw=None,
                theme={"theme": cfg.theme, "intensity": cfg.intensity},
            )

        # /theme set <name...> <light|heavy?>
        if len(parts) >= 3 and parts[1].lower() == "set":
            intensity = "light"
            if parts[-1].lower() in ("light", "heavy"):
                intensity = parts[-1].lower()
                theme_name = " ".join(parts[2:-1]).strip()
            else:
                theme_name = " ".join(parts[2:]).strip()

            if not theme_name:
                raise HTTPException(status_code=422, detail="Theme name is required (example: /theme set Warhammer 40k light)")

            cfg = save_theme(theme_name, intensity)
            return AskResponse(
                ok=True,
                topic="theme",
                answer=f"Theme set to: {cfg.theme} ({cfg.intensity}).",
                duration_s=time.time() - t0,
                error=None,
                raw=None,
                theme={"theme": cfg.theme, "intensity": cfg.intensity},
            )

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    # normal ask
    normalized = _normalize_topic(text)
    raw_res = await _run_brain_repl(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))

    # fallback if it’s clearly junk
    topic_key = normalized.strip().lower()
    if _looks_low_quality(cleaned):
        if topic_key in FALLBACK_MINI:
            cleaned = FALLBACK_MINI[topic_key]
        else:
            cleaned = (
                f"{normalized}\n\n"
                "I don’t have a clean answer stored yet.\n\n"
                "Next:\n"
                "- Run learning (safe): /run/webqueue?limit=3&confirm=true\n"
                "- Or in brain.py: /weblearn <topic>\n"
            ).strip()

    # apply theme wrapper
    cfg = load_theme()
    themed = apply_theme(cleaned, topic=normalized, cfg=cfg)

    return AskResponse(
        ok=True,
        topic=topic_key,
        answer=themed,
        duration_s=float(raw_res.get("duration_s", time.time() - t0)),
        error=None,
        raw=(raw_res if req.raw else None),
        theme={"theme": cfg.theme, "intensity": cfg.intensity},
    )
