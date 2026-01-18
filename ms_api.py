#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ms_theme import apply_theme, load_theme, save_theme, ui_intensity_choices

APP_NAME = "MachineSpirit API"
VERSION = "0.3.12"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()
PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")

KNOWLEDGE_PATH = Path(os.environ.get("MS_KNOWLEDGE_PATH", str(REPO_DIR / "data" / "local_knowledge.json"))).resolve()

MS_API_KEY = (os.environ.get("MS_API_KEY", "") or "").strip()
AUTO_WEBLEARN = (os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() not in ("0", "false", "no", "off"))

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
    theme: Optional[Dict[str, Any]] = None


class ThemeRequest(BaseModel):
    theme: str
    intensity: str


class OverrideRequest(BaseModel):
    topic: str
    answer: str


# ----------------------------
# Auth
# ----------------------------
def _require_auth(request: Request) -> None:
    if not MS_API_KEY:
        raise HTTPException(status_code=500, detail="MS_API_KEY is not set on server")
    key = (request.headers.get("x-api-key", "") or "").strip()
    if key != MS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ----------------------------
# Small utils
# ----------------------------
def _iso_now() -> str:
    import datetime as dt
    return dt.datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _normalize_topic(text: str) -> str:
    s = (text or "").strip()

    # If someone accidentally sends JSON string like {"text":"subnet mask"}
    m = re.match(r'^\s*\{\s*"text"\s*:\s*"(.+?)"\s*\}\s*$', s)
    if m:
        s = m.group(1)

    s = re.sub(r"^\s*(what is|what's|what are|define|explain)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _topic_key(topic: str) -> str:
    return _normalize_topic(topic).lower()


def _looks_low_quality(ans: str) -> bool:
    a = (ans or "").strip().lower()
    if not a:
        return True

    bad_snips = [
        "all rights reserved",
        "close global navigation menu",
        "captcha",
        "we apologize for the inconvenience",
        "refusing (junk topic)",
        "i couldn't save a clean answer yet",
        "i tried researching that, but the sources i found were too low-quality",
        "machine spirit: i do not have a taught answer for that yet",
    ]
    for b in bad_snips:
        if b in a:
            return True

    # huge “nav dump” smell
    if a.count("menu") >= 3 and len(a) > 500:
        return True

    return False


# ----------------------------
# Brain subprocess helpers
# ----------------------------
def _brain_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]


async def _run_brain_one_line(line: str, timeout_s: int) -> Dict[str, Any]:
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
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="brain.py timed out")

    dt = time.time() - t0
    stdout = (out_b or b"").decode("utf-8", errors="replace")
    stderr = (err_b or b"").decode("utf-8", errors="replace")
    rc = int(proc.returncode or 0)
    return {"exit_code": rc, "duration_s": dt, "stdout": stdout, "stderr": stderr}


def _clean_repl_stdout(raw: str) -> str:
    """
    Clean brain REPL stdout into a usable answer.
    Keeps the printed content after the first prompt line.
    """
    if not raw:
        return ""
    lines = raw.splitlines()

    # drop banner lines
    out: List[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("Machine Spirit brain online."):
            continue
        out.append(ln.rstrip())

    # Try to remove the first prompt line if present
    # Example:
    # > topic
    # ANSWER...
    if out and re.match(r"^\s*>\s*", out[0]):
        out = out[1:]

    # remove trailing prompt echoes (rare)
    while out and re.match(r"^\s*>\s*", out[-1]):
        out.pop()

    return "\n".join(out).strip()


# ----------------------------
# Knowledge helpers
# ----------------------------
def _get_entry(topic_key: str) -> Optional[Dict[str, Any]]:
    db = _read_json(KNOWLEDGE_PATH, {})
    if not isinstance(db, dict):
        return None
    e = db.get(topic_key)
    return e if isinstance(e, dict) else None


def _save_entry(topic_key: str, entry: Dict[str, Any]) -> None:
    db = _read_json(KNOWLEDGE_PATH, {})
    if not isinstance(db, dict):
        db = {}
    db[topic_key] = entry
    _write_json_atomic(KNOWLEDGE_PATH, db)


def _entry_is_good(e: Dict[str, Any]) -> bool:
    ans = (e.get("answer") or "").strip()
    if not ans:
        return False
    if _looks_low_quality(ans):
        return False
    try:
        conf = float(e.get("confidence", 0.0) or 0.0)
    except Exception:
        conf = 0.0
    taught = bool(e.get("taught_by_user", False))
    # user-taught always wins; otherwise require decent confidence
    return taught or conf >= 0.70


# ----------------------------
# Wikipedia fallback (for general “anything” mode)
# ----------------------------
def _wiki_opensearch(q: str) -> Optional[str]:
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {"action": "opensearch", "search": q, "limit": 1, "namespace": 0, "format": "json"}
        r = requests.get(url, params=params, timeout=8)
        data = r.json()
        titles = data[1] if isinstance(data, list) and len(data) >= 2 else []
        if titles:
            return str(titles[0])
    except Exception:
        return None
    return None


def _wiki_summary(title: str) -> Optional[Tuple[str, str]]:
    try:
        safe = urllib.parse.quote(title.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "MachineSpirit/1.0"})
        if r.status_code != 200:
            return None
        j = r.json()
        extract = (j.get("extract") or "").strip()
        page = ""
        try:
            page = j.get("content_urls", {}).get("desktop", {}).get("page", "") or ""
        except Exception:
            page = ""
        if extract:
            return extract, page
    except Exception:
        return None
    return None


def _make_simple_answer(title: str, extract: str, page_url: str) -> str:
    lines: List[str] = []
    lines.append(title.upper())
    lines.append("")
    lines.append("Definition:")
    lines.append(f"- {extract}")
    lines.append("")
    lines.append("Sources:")
    if page_url:
        lines.append(f"- Wikipedia: {page_url}")
    else:
        lines.append(f"- Wikipedia: {title}")
    return "\n".join(lines).strip()


def _wiki_fallback_learn(topic: str) -> Optional[str]:
    title = _wiki_opensearch(topic) or topic
    summ = _wiki_summary(title)
    if not summ:
        return None
    extract, page_url = summ
    return _make_simple_answer(title, extract, page_url)


# ----------------------------
# Routes
# ----------------------------
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
        "brain_path": str(BRAIN_PATH),
        "repo_dir": str(REPO_DIR),
        "python": PYTHON_BIN,
        "knowledge_path": str(KNOWLEDGE_PATH),
        "auto_weblearn": AUTO_WEBLEARN,
        "theme": {"theme": cfg.theme, "intensity": cfg.intensity},
    }


@app.get("/theme")
async def get_theme(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    cfg = load_theme()
    return {"ok": True, "theme": cfg.theme, "intensity": cfg.intensity, "choices": ui_intensity_choices()}


@app.post("/theme")
async def set_theme_endpoint(request: Request, payload: ThemeRequest) -> Dict[str, Any]:
    _require_auth(request)
    cfg = save_theme(payload.theme, payload.intensity)
    return {"ok": True, "theme": cfg.theme, "intensity": cfg.intensity}


@app.post("/override")
async def override_endpoint(request: Request, payload: OverrideRequest) -> Dict[str, Any]:
    _require_auth(request)

    topic = _normalize_topic(payload.topic)
    ans = (payload.answer or "").strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic is required")
    if not ans:
        raise HTTPException(status_code=422, detail="answer is required")
    if len(ans) > 6000:
        raise HTTPException(status_code=422, detail="answer too long (limit 6000 chars)")

    k = _topic_key(topic)
    e = _get_entry(k) or {}
    e["answer"] = ans
    e["taught_by_user"] = True
    e["notes"] = "override via UI conversation"
    e["updated"] = _iso_now()
    try:
        old_c = float(e.get("confidence", 0.0) or 0.0)
    except Exception:
        old_c = 0.0
    e["confidence"] = max(old_c, 0.90)
    if not isinstance(e.get("sources"), list):
        e["sources"] = []

    _save_entry(k, e)
    return {"ok": True, "topic": k}


@app.post("/ask", response_model=AskResponse)
async def ask(request: Request, req: AskRequest) -> AskResponse:
    _require_auth(request)

    t0 = time.time()
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")

    # theme commands are handled here too (same as before)
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
            return AskResponse(ok=True, topic="theme", answer=msg.strip(), duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

        if len(parts) >= 2 and parts[1].lower() in ("off", "none", "disable", "disabled"):
            cfg = save_theme("none", "light")
            return AskResponse(ok=True, topic="theme", answer="Theme disabled.", duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

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
            return AskResponse(ok=True, topic="theme", answer=f"Theme set to: {cfg.theme} ({cfg.intensity}).", duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    normalized = _normalize_topic(text)
    key = _topic_key(normalized)

    # Special-case: name
    if key in ("my name", "what is my name"):
        e = _get_entry("my name")
        if e and (e.get("answer") or "").strip():
            ans = f"Your name is {e['answer'].strip()}."
            cfg = load_theme()
            return AskResponse(ok=True, topic="my name", answer=apply_theme(ans, topic="my name", cfg=cfg), duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})
        else:
            cfg = load_theme()
            msg = 'I don’t know your name yet. Tell me: “my name is Aaron” (or whatever you want), and I’ll remember it.'
            return AskResponse(ok=True, topic="my name", answer=apply_theme(msg, topic="my name", cfg=cfg), duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # 1) If we already have a good saved answer, reuse it (prevents bouncing to random sources)
    existing = _get_entry(key)
    if existing and _entry_is_good(existing):
        cfg = load_theme()
        out = existing.get("answer", "").strip()
        return AskResponse(ok=True, topic=key, answer=apply_theme(out, topic=normalized, cfg=cfg), duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # 2) Ask brain normally
    raw1 = await _run_brain_one_line(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned1 = _clean_repl_stdout(raw1.get("stdout", ""))

    # If brain gave a decent answer, return it (and if it saved it, next time we’ll reuse it)
    if cleaned1 and (not _looks_low_quality(cleaned1)):
        cfg = load_theme()
        return AskResponse(ok=True, topic=key, answer=apply_theme(cleaned1, topic=normalized, cfg=cfg), duration_s=float(raw1.get("duration_s", time.time() - t0)), raw=(raw1 if req.raw else None), theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # 3) Auto research (unrestricted mode)
    if AUTO_WEBLEARN:
        raw2 = await _run_brain_one_line(f"/weblearn {normalized}", timeout_s=int(req.timeout_s or 25))
        cleaned2 = _clean_repl_stdout(raw2.get("stdout", ""))

        # check saved knowledge again (preferred)
        saved = _get_entry(key)
        if saved and (saved.get("answer") or "").strip() and (not _looks_low_quality(saved.get("answer", ""))):
            cfg = load_theme()
            return AskResponse(ok=True, topic=key, answer=apply_theme(saved["answer"].strip(), topic=normalized, cfg=cfg), duration_s=float(raw2.get("duration_s", time.time() - t0)), raw=(raw2 if req.raw else None), theme={"theme": cfg.theme, "intensity": cfg.intensity})

        # if brain output is good enough, use it even if it didn't store cleanly
        if cleaned2 and (not _looks_low_quality(cleaned2)):
            cfg = load_theme()
            return AskResponse(ok=True, topic=key, answer=apply_theme(cleaned2, topic=normalized, cfg=cfg), duration_s=float(raw2.get("duration_s", time.time() - t0)), raw=(raw2 if req.raw else None), theme={"theme": cfg.theme, "intensity": cfg.intensity})

        # 4) Wikipedia fallback if web results are garbage
        wf = _wiki_fallback_learn(normalized)
        if wf:
            entry = saved if isinstance(saved, dict) else {}
            entry["answer"] = wf
            entry["taught_by_user"] = False
            entry["updated"] = _iso_now()
            entry["confidence"] = max(float(entry.get("confidence", 0.0) or 0.0), 0.70)
            if not isinstance(entry.get("sources"), list):
                entry["sources"] = []
            _save_entry(key, entry)

            cfg = load_theme()
            return AskResponse(ok=True, topic=key, answer=apply_theme(wf, topic=normalized, cfg=cfg), duration_s=time.time() - t0, raw=(raw2 if req.raw else None), theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # final fallback
    cfg = load_theme()
    msg = (
        f"{normalized}\n\n"
        "I couldn’t save a clean answer yet.\n\n"
        "Try asking with a little more detail (example: add 'video game' or 'computer hardware')."
    )
    return AskResponse(ok=True, topic=key, answer=apply_theme(msg, topic=normalized, cfg=cfg), duration_s=time.time() - t0, raw=(raw1 if req.raw else None), theme={"theme": cfg.theme, "intensity": cfg.intensity})
