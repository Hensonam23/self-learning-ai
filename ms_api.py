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

from ms_theme import apply_theme, load_theme, save_theme, ui_intensity_choices

APP_NAME = "MachineSpirit API"
VERSION = "0.3.8"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")
MS_API_KEY = os.environ.get("MS_API_KEY", "")
LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))

# Auto research (default ON)
AUTO_WEBLEARN = os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() in ("1", "true", "yes", "on")
AUTO_WEBLEARN_TIMEOUT_S = int(os.environ.get("MS_AUTO_WEBLEARN_TIMEOUT_S", "80"))
AUTO_WEBLEARN_MAX_ATTEMPTS = int(os.environ.get("MS_AUTO_WEBLEARN_MAX_ATTEMPTS", "3"))
AUTO_WEBLEARN_MIN_LEN = int(os.environ.get("MS_AUTO_WEBLEARN_MIN_LEN", "140"))

app = FastAPI(title=APP_NAME, version=VERSION)

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

def _require_auth(request: Request) -> None:
    if not MS_API_KEY:
        raise HTTPException(status_code=500, detail="MS_API_KEY is not set on server")
    key = request.headers.get("x-api-key", "")
    if key != MS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def _brain_repl_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]

def _normalize_topic(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", s, flags=re.IGNORECASE).strip()
    s = s.strip().strip('"\'')

    # strip trailing punctuation like "fallout 3?" -> "fallout 3"
    s = re.sub(r"[?!\.]+$", "", s).strip()
    return s

def _clean_repl_stdout(raw: str) -> str:
    if not raw:
        return ""
    prompt_re = re.compile(r"^\s*>\s*(.*)$")
    lines = raw.splitlines()

    topic = ""
    body: List[str] = []
    saw_topic = False

    for line in lines:
        s = line.strip()

        if s.startswith("Machine Spirit brain online."):
            continue

        pm = prompt_re.match(line)
        if pm:
            prompt_text = (pm.group(1) or "").strip()
            if "shutting down" in prompt_text.lower():
                break
            if (not saw_topic) and prompt_text:
                topic = prompt_text
                saw_topic = True
                continue
            break

        if "shutting down" in s.lower():
            continue

        body.append(line.rstrip())

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

def _looks_like_nav_legal_junk(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    if "all rights reserved" in t:
        return True
    if "close global navigation" in t:
        return True
    if "log in" in t and "sign up" in t:
        return True

    menu_words = ["games", "shop", "support", "community", "news", "account", "redeem code", "merchandise"]
    hits = sum(t.count(w) for w in menu_words)
    if hits >= 8:
        return True
    return False

def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip()
    al = a.lower()

    if not a:
        return True
    if len(a) < AUTO_WEBLEARN_MIN_LEN:
        return True
    if "i do not have a taught answer" in al:
        return True
    if "ask using a normal topic name instead" in al:
        return True
    if "may refer to" in al:
        return True
    if _looks_like_block_or_captcha(a):
        return True
    if _looks_like_nav_legal_junk(a):
        return True
    return False

def _extract_first_source_domain(answer: str) -> str:
    if not answer:
        return ""
    m = re.search(r"https?://([A-Za-z0-9\.\-]+)", answer)
    if not m:
        return ""
    d = (m.group(1) or "").lower().strip().lstrip(".")
    return d

async def _run_brain(one_line: str, timeout_s: int) -> Dict[str, Any]:
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
            "max_attempts": AUTO_WEBLEARN_MAX_ATTEMPTS,
            "timeout_s": AUTO_WEBLEARN_TIMEOUT_S,
            "min_len": AUTO_WEBLEARN_MIN_LEN,
        },
    }

@app.get("/theme")
async def get_theme(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    cfg = load_theme()
    choices = ui_intensity_choices()
    return {"ok": True, "theme": cfg.theme, "intensity": cfg.intensity, "choices": choices}

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

    # Theme commands still supported
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
            return AskResponse(ok=True, topic="theme", answer=msg.strip(), duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity}, did_research=False)

        if len(parts) >= 2 and parts[1].lower() in ("off", "none", "disable", "disabled"):
            cfg = save_theme("none", "light")
            return AskResponse(ok=True, topic="theme", answer="Theme disabled.", duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity}, did_research=False)

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
            return AskResponse(ok=True, topic="theme", answer=f"Theme set to: {cfg.theme} ({cfg.intensity}).",
                               duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False)

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    normalized = _normalize_topic(text)
    topic_key = normalized.lower().strip()

    # 1) Ask brain first
    raw_res = await _run_brain(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))

    did_research = False

    # 2) If junk, auto-research with retries and domain avoidance
    if AUTO_WEBLEARN and _looks_low_quality(cleaned):
        did_research = True

        avoid: List[str] = []
        last_answer = cleaned

        for attempt in range(1, max(1, AUTO_WEBLEARN_MAX_ATTEMPTS) + 1):
            if attempt == 1:
                q = normalized
            else:
                bad_domain = _extract_first_source_domain(last_answer)
                if bad_domain and bad_domain not in avoid:
                    avoid.append(bad_domain)

                # always avoid these once we see junk from game marketing sites
                for d in ("bethesda.net", "fallout.bethesda.net"):
                    if d not in avoid:
                        avoid.append(d)

                suffix = "overview" if attempt == 2 else "summary"
                neg = " ".join([f"-site:{d}" for d in avoid if d])
                q = f"{normalized} {suffix} {neg}".strip()

            cmd = f"/weblearn {q}"
            raw_learn = await _run_brain(cmd, timeout_s=AUTO_WEBLEARN_TIMEOUT_S)
            learned = _clean_repl_stdout(raw_learn.get("stdout", ""))

            if learned.strip():
                last_answer = learned

            if not _looks_low_quality(learned):
                cleaned = learned
                raw_res = raw_learn
                break

        # Still junk? return a clean message instead of garbage.
        if _looks_like_block_or_captcha(last_answer):
            cleaned = (
                f"{normalized}\n\n"
                "I hit a blocked/captcha page while researching that, so I couldn’t pull a clean explanation right now.\n"
                "Try again in a minute, or ask with a cleaner source.\n"
            ).strip()
        elif _looks_like_nav_legal_junk(last_answer):
            cleaned = (
                f"{normalized}\n\n"
                "I found a page that was mostly navigation/legal text, not a real explanation. I retried, but didn’t get a clean source yet.\n"
                "Ask again and I’ll keep searching.\n"
            ).strip()

    cfg = load_theme()
    themed = apply_theme(cleaned, topic=normalized, cfg=cfg)

    return AskResponse(
        ok=True,
        topic=topic_key,
        answer=themed,
        duration_s=float(raw_res.get("duration_s", time.time() - t0)),
        raw=(raw_res if req.raw else None),
        theme={"theme": cfg.theme, "intensity": cfg.intensity},
        did_research=did_research,
    )
