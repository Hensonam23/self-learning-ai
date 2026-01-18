#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ms_theme import apply_theme, load_theme, save_theme, ui_intensity_choices

APP_NAME = "MachineSpirit API"
VERSION = "0.3.7"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()
PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))
KNOWLEDGE_PATH = Path(
    os.environ.get("MS_KNOWLEDGE_PATH", str(REPO_DIR / "data" / "local_knowledge.json"))
).resolve()

MS_API_KEY = (os.environ.get("MS_API_KEY", "") or "").strip()
AUTO_WEBLEARN = (os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() not in ("0", "false", "no", "off"))

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

class ThemeRequest(BaseModel):
    theme: str
    intensity: str

def _require_auth(request: Request) -> None:
    if not MS_API_KEY:
        raise HTTPException(status_code=500, detail="MS_API_KEY is not set on server")
    key = (request.headers.get("x-api-key", "") or "").strip()
    if key != MS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default

def _load_entry(topic_key: str) -> Dict[str, Any]:
    db = _read_json(KNOWLEDGE_PATH, {})
    if not isinstance(db, dict):
        return {}
    ent = db.get((topic_key or "").strip().lower())
    return ent if isinstance(ent, dict) else {}

def _has_wikipedia_source(ent: Dict[str, Any]) -> bool:
    try:
        srcs = ent.get("sources") or []
        if not isinstance(srcs, list):
            return False
        for s in srcs:
            s = (s or "")
            if "wikipedia.org/wiki/" in s:
                return True
    except Exception:
        return False
    return False

def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return True

    bad_snips = [
        "all rights reserved",
        "close global navigation",
        "log in / sign up",
        "redeem code",
        "account management",
        "captcha",
        "we apologize for the inconvenience",
        "your activity and behavior on this site made us think that you are a bot",
    ]
    for b in bad_snips:
        if b in a:
            return True

    if "may refer to" in a:
        return True
    if a.endswith("sources:\n-") or a.endswith("sources:\n- "):
        return True

    return False

def _is_protected_entry(ent: Dict[str, Any]) -> bool:
    """
    Prevents flip-flopping back to random web/news pages once we have a decent saved answer.
    """
    try:
        if not isinstance(ent, dict):
            return False

        if ent.get("taught_by_user") is True:
            return True

        c = float(ent.get("confidence", 0.0) or 0.0)
        if c >= 0.80:
            return True

        # Treat Wikipedia definitions as "curated enough" once present + not junk
        if _has_wikipedia_source(ent):
            ans = (ent.get("answer") or "").strip()
            if ans and not _looks_low_quality(ans):
                return True
    except Exception:
        return False

    return False

def _brain_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]

def _normalize_topic(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"[?!.]+$", "", s).strip()
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

            # ignore command prompt as "topic"
            if (not saw_topic) and prompt_text.startswith("/"):
                continue

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

async def _run_brain_once(line: str, timeout_s: int) -> Dict[str, Any]:
    LOCK_PATH.touch(exist_ok=True)

    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        *_brain_args(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_DIR),
    )

    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(input=(line + "\n").encode("utf-8")),
            timeout=max(5, int(timeout_s)),
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="brain.py timed out")

    dt = time.time() - t0
    stdout = (out_b or b"").decode("utf-8", errors="replace")
    stderr = (err_b or b"").decode("utf-8", errors="replace")

    return {
        "duration_s": dt,
        "stdout": stdout,
        "stderr": stderr,
        "args": _brain_args(),
        "exit_code": int(proc.returncode or 0),
    }

async def _brain_answer(topic: str, timeout_s: int) -> Tuple[str, Dict[str, Any]]:
    raw_res = await _run_brain_once(topic, timeout_s=timeout_s)
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))
    return cleaned, raw_res

async def _auto_weblearn(topic: str, timeout_s: int) -> None:
    t = max(10, min(60, int(timeout_s) * 2))
    await _run_brain_once(f"/weblearn {topic}", timeout_s=t)

@app.get("/")
async def root() -> Dict[str, Any]:
    return {"ok": True, "app": APP_NAME, "version": VERSION}

@app.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    cfg = load_theme()
    return {
        "ok": True,
        "app": APP_NAME,
        "version": VERSION,
        "repo_dir": str(REPO_DIR),
        "brain_path": str(BRAIN_PATH),
        "python": PYTHON_BIN,
        "auto_weblearn": AUTO_WEBLEARN,
        "theme": {"theme": cfg.theme, "intensity": cfg.intensity},
    }

@app.get("/theme")
async def get_theme(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    cfg = load_theme()
    return {
        "ok": True,
        "theme": cfg.theme,
        "intensity": cfg.intensity,
        "choices": ui_intensity_choices(),
    }

@app.post("/theme")
async def set_theme(request: Request, payload: ThemeRequest) -> Dict[str, Any]:
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

    # /theme commands in chat
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
                               theme={"theme": cfg.theme, "intensity": cfg.intensity})

        if len(parts) >= 2 and parts[1].lower() in ("off", "none", "disable", "disabled"):
            cfg = save_theme("none", "light")
            return AskResponse(ok=True, topic="theme", answer="Theme disabled.", duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity})

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
                               duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    normalized = _normalize_topic(text)
    topic_key = normalized.strip().lower()

    # If we already have a protected saved answer, return it (prevents flip-flop to random news pages)
    existing = _load_entry(topic_key)
    if _is_protected_entry(existing):
        saved = (existing.get("answer") or "").strip()
        if saved:
            cfg = load_theme()
            themed = apply_theme(saved, topic=normalized, cfg=cfg)
            return AskResponse(ok=True, topic=topic_key, answer=themed, duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # Ask brain normally
    cleaned, raw_res = await _brain_answer(normalized, timeout_s=int(req.timeout_s or 25))

    # If junk and auto-weblearn on: learn then ask again
    if _looks_low_quality(cleaned) and AUTO_WEBLEARN:
        try:
            await _auto_weblearn(normalized, timeout_s=int(req.timeout_s or 25))
            cleaned2, raw_res2 = await _brain_answer(normalized, timeout_s=int(req.timeout_s or 25))
            if cleaned2 and not _looks_low_quality(cleaned2):
                cleaned = cleaned2
                raw_res = raw_res2
        except Exception:
            pass

    if _looks_low_quality(cleaned):
        cleaned = (
            f"{normalized}\n\n"
            "I don’t have a clean answer stored yet.\n\n"
            "Fix it by replying in the UI with:\n"
            "  no its: <your corrected answer>\n"
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
    )
