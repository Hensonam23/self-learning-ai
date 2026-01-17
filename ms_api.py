#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ms_theme import apply_theme, load_theme, save_theme, ui_intensity_choices


APP_NAME = "MachineSpirit API"
VERSION = "0.3.6"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")
MS_API_KEY = os.environ.get("MS_API_KEY", "")

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))

# "Unrestricted-ish" web learning (automatic)
AUTO_WEBLEARN = os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() in ("1", "true", "yes", "on")
AUTO_WEBLEARN_MAX_TRIES = int(os.environ.get("MS_AUTO_WEBLEARN_MAX_TRIES", "2"))
AUTO_WEBLEARN_TIMEOUT_S = int(os.environ.get("MS_AUTO_WEBLEARN_TIMEOUT_S", "60"))
AUTO_WEBLEARN_MIN_LEN = int(os.environ.get("MS_AUTO_WEBLEARN_MIN_LEN", "140"))


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
    did_research: Optional[bool] = None


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
# Brain subprocess helpers
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


def _looks_like_block_or_captcha(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    markers = [
        "we apologize for the inconvenience",
        "made us think that you are a bot",
        "captcha",
        "cloudflare",
        "radware",
        "request unblock",
        "access denied",
        "unusual traffic",
        "verify you are human",
        "attention required",
    ]
    return any(m in t for m in markers)


def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip()
    al = a.lower()

    if not a:
        return True

    # brain disclaimers / no-answer patterns
    if "i do not have a taught answer" in al:
        return True
    if "ask using a normal topic name instead" in al:
        return True
    if "if my reply is wrong or weak, correct me" in al:
        return True

    # junk patterns
    if "may refer to" in al:
        return True
    if al.endswith("sources:\n-") or al.endswith("sources:\n- "):
        return True

    # blocked/captcha pages
    if _looks_like_block_or_captcha(a):
        return True

    # super short answers are usually not useful
    if len(a) < 40:
        return True

    return False


async def _run_brain_repl(one_line: str, timeout_s: int) -> Dict[str, Any]:
    """
    Runs brain.py as a subprocess, feeds one line, reads stdout/stderr.
    """
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
            proc.communicate(input=(one_line + "\n").encode("utf-8")),
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
        "input": one_line,
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
        "ui_hint": "Main UI is on port 8020 (/ui).",
        "auto_weblearn": {
            "enabled": AUTO_WEBLEARN,
            "max_tries": AUTO_WEBLEARN_MAX_TRIES,
            "timeout_s": AUTO_WEBLEARN_TIMEOUT_S,
            "min_len": AUTO_WEBLEARN_MIN_LEN,
        },
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
        "auto_weblearn": {
            "enabled": AUTO_WEBLEARN,
            "max_tries": AUTO_WEBLEARN_MAX_TRIES,
            "timeout_s": AUTO_WEBLEARN_TIMEOUT_S,
            "min_len": AUTO_WEBLEARN_MIN_LEN,
        },
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

    # Allow /theme commands through UI/API chat too
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
                did_research=False,
            )

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
                did_research=False,
            )

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
                did_research=False,
            )

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    # Normal ask
    normalized = _normalize_topic(text)
    topic_key = normalized.lower().strip()

    # 1) Try normal memory answer first
    raw_res = await _run_brain_repl(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))

    did_research = False

    # 2) If weak / blocked / missing, auto-run web learn (no extra commands needed)
    if AUTO_WEBLEARN and _looks_low_quality(cleaned):
        for attempt in range(max(1, AUTO_WEBLEARN_MAX_TRIES)):
            did_research = True
            learn_cmd = f"/weblearn {normalized}"
            raw_learn = await _run_brain_repl(learn_cmd, timeout_s=AUTO_WEBLEARN_TIMEOUT_S)
            learned = _clean_repl_stdout(raw_learn.get("stdout", ""))

            # If learning succeeded and looks decent, use it
            if (not _looks_low_quality(learned)) and (len(learned) >= AUTO_WEBLEARN_MIN_LEN):
                cleaned = learned
                raw_res = raw_learn if req.raw else raw_res
                break

            # If still garbage/captcha, keep looping (second try sometimes lands on a different source)
            cleaned = learned if learned.strip() else cleaned

        # If still junk, at least tell the truth (don’t silently store captcha garbage)
        if _looks_like_block_or_captcha(cleaned):
            cleaned = (
                f"{normalized}\n\n"
                "That site is blocking automated access (captcha/blocked page), so I couldn’t pull a clean answer right now.\n\n"
                "If you want, ask again in a minute, or give me a specific URL from a normal docs page (Cisco/Juniper/IETF/etc) and I’ll learn from it.\n"
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
        did_research=did_research,
    )
