#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ms_theme import apply_theme, load_theme, save_theme, ui_intensity_choices

APP_NAME = "MachineSpirit API"
VERSION = "0.3.9"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")
MS_API_KEY = (os.environ.get("MS_API_KEY", "") or "").strip()

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))
KNOWLEDGE_PATH = Path(
    os.environ.get("MS_KNOWLEDGE_PATH", str(REPO_DIR / "data" / "local_knowledge.json"))
).resolve()

AUTO_WEBLEARN = (os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() not in ("0", "false", "no", "off"))

app = FastAPI(title=APP_NAME, version=VERSION)

# single-process concurrency guard (uvicorn can run multiple workers, but your systemd unit uses 1)
_BRAIN_LOCK = asyncio.Lock()


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
# JSON helpers
# ----------------------------
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


def _iso_now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# ----------------------------
# Normalization + quality checks
# ----------------------------
def _normalize_topic(text: str) -> str:
    s = (text or "").strip()

    # handle accidental JSON-ish inputs like {"text":"subnet mask"}
    m = re.match(r'^\s*\{\s*"text"\s*:\s*"(.+?)"\s*\}\s*$', s)
    if m:
        s = m.group(1).strip()

    s = re.sub(r"^\s*(what is|what's|define|explain|tell me|give me)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[?!.]+$", "", s).strip()
    return s


def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return True

    # “I couldn't save a clean answer yet”
    if "couldn't save a clean answer" in a:
        return True
    if "i tried researching that" in a:
        return True
    if "try asking with a little more detail" in a:
        return True
    if "i do not have a taught answer for that yet" in a:
        return True

    # obvious scrape junk
    if "captcha" in a:
        return True
    if "all rights reserved" in a:
        return True
    if "close global navigation menu" in a:
        return True

    return False


# ----------------------------
# Stable knowledge (local_knowledge.json)
# ----------------------------
def _get_entry(topic_key: str) -> Optional[Dict[str, Any]]:
    db = _read_json(KNOWLEDGE_PATH, {})
    if not isinstance(db, dict):
        return None
    e = db.get(topic_key)
    return e if isinstance(e, dict) else None


def _find_stable_answer(topic_key: str) -> Optional[str]:
    # try a few keys (helps when punctuation or wording differs)
    keys = []
    t = (topic_key or "").strip().lower()
    if t:
        keys.append(t)
        keys.append(t.replace("  ", " "))
        keys.append(re.sub(r"\s+", " ", t).strip())
        keys.append(t.rstrip("?").strip())

    seen = set()
    for k in keys:
        if not k or k in seen:
            continue
        seen.add(k)
        e = _get_entry(k)
        if not e:
            continue
        ans = e.get("answer")
        if isinstance(ans, str) and ans.strip():
            return ans.strip()
    return None


def _override_knowledge(topic: str, new_answer: str, note: str = "") -> Tuple[bool, str]:
    topic_k = _normalize_topic(topic).lower().strip()
    ans = (new_answer or "").strip()
    if not topic_k:
        return False, "missing topic"
    if not ans:
        return False, "missing answer"

    db = _read_json(KNOWLEDGE_PATH, {})
    if not isinstance(db, dict):
        db = {}

    entry = db.get(topic_k)
    if not isinstance(entry, dict):
        entry = {}

    entry["answer"] = ans
    entry["taught_by_user"] = True
    entry["notes"] = note or "override via API"
    entry["updated"] = _iso_now()

    try:
        old_c = float(entry.get("confidence", 0.0) or 0.0)
    except Exception:
        old_c = 0.0
    entry["confidence"] = max(old_c, 0.90)

    if not isinstance(entry.get("sources"), list):
        entry["sources"] = []

    db[topic_k] = entry
    _write_json_atomic(KNOWLEDGE_PATH, db)
    return True, topic_k


# ----------------------------
# Local facts router (NO WEB)
# ----------------------------
def _local_facts_answer(text: str) -> Optional[Tuple[str, str]]:
    s = (text or "").strip()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[?!.]+$", "", s).strip()
    s2 = _normalize_topic(s).lower()

    now = _dt.datetime.now()

    def dnum() -> str:
        return now.strftime("%d").lstrip("0") or "0"

    def hour12() -> str:
        h = now.strftime("%I").lstrip("0")
        return h if h else "12"

    # date
    if s2 in ("date", "today date", "todays date", "today's date", "current date", "what date is it"):
        ans = (
            "DATE\n\n"
            "Definition:\n"
            f"- Today’s date is {now.strftime('%B')} {dnum()}, {now.strftime('%Y')}.\n\n"
            "Sources:\n"
            "- local system clock\n"
        )
        return ("date", ans)

    # day
    if s2 in ("day", "day of week", "day of the week", "what day is it", "what day is it today"):
        ans = (
            "DAY OF WEEK\n\n"
            "Definition:\n"
            f"- Today is {now.strftime('%A')}, {now.strftime('%B')} {dnum()}, {now.strftime('%Y')}.\n\n"
            "Sources:\n"
            "- local system clock\n"
        )
        return ("day", ans)

    # time
    if s2 in ("time", "current time", "what time is it", "what is the time"):
        tz = (now.strftime("%Z") or "").strip()
        suffix = f" {tz}" if tz else ""
        ans = (
            "TIME\n\n"
            "Definition:\n"
            f"- It’s {hour12()}:{now.strftime('%M')} {now.strftime('%p')}{suffix} right now.\n\n"
            "Sources:\n"
            "- local system clock\n"
        )
        return ("time", ans)

    return None


# ----------------------------
# Brain subprocess helpers
# ----------------------------
def _brain_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]


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
    if body_text:
        return body_text.strip()
    return topic.strip()


async def _run_brain(line: str, timeout_s: int) -> Dict[str, Any]:
    LOCK_PATH.touch(exist_ok=True)

    async with _BRAIN_LOCK:
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
        rc = int(proc.returncode or 0)

        return {"exit_code": rc, "duration_s": dt, "stdout": stdout, "stderr": stderr, "args": _brain_args()}


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
        "repo_dir": str(REPO_DIR),
        "brain_path": str(BRAIN_PATH),
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

    # 1) Theme chat commands (optional)
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

    # 2) Local facts (date/time/day) — never web
    lf = _local_facts_answer(text)
    if lf:
        topic_k, ans = lf
        cfg = load_theme()
        themed = apply_theme(ans.strip(), topic=topic_k, cfg=cfg)
        return AskResponse(ok=True, topic=topic_k, answer=themed, duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # 3) Conversational "my name is X" save (works in any client, not just UI)
    m = re.match(r"^\s*my\s+name\s+is\s+(.+?)\s*$", text, flags=re.IGNORECASE)
    if m:
        name = m.group(1).strip().strip('"').strip("'")
        ok, key_or_err = _override_knowledge("my name", name, note="set via conversation (API)")
        cfg = load_theme()
        msg = f'Got it — your name is saved as "{name}".' if ok else f"Could not save name: {key_or_err}"
        themed = apply_theme(msg, topic="my name", cfg=cfg)
        return AskResponse(ok=True, topic="my name", answer=themed, duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

    m2 = re.match(r"^\s*your\s+name\s+is\s+(.+?)\s*$", text, flags=re.IGNORECASE)
    if m2:
        nm = m2.group(1).strip().strip('"').strip("'")
        ok, key_or_err = _override_knowledge("your name", nm, note="set via conversation (API)")
        cfg = load_theme()
        msg = f'Got it — my name is saved as "{nm}".' if ok else f"Could not save my name: {key_or_err}"
        themed = apply_theme(msg, topic="your name", cfg=cfg)
        return AskResponse(ok=True, topic="your name", answer=themed, duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

    normalized = _normalize_topic(text)
    topic_key = normalized.lower().strip()

    # 4) Stable answer wins (prevents “it went back to the other crap answer”)
    stable = _find_stable_answer(topic_key)
    if stable:
        cfg = load_theme()
        # avoid double-wrapping if already themed
        themed = stable if "+++ VOX-CAST" in stable else apply_theme(stable, topic=topic_key, cfg=cfg)
        return AskResponse(ok=True, topic=topic_key, answer=themed, duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # 5) Ask brain (may suggest alias / may refuse / may answer)
    raw_res = await _run_brain(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))

    # 6) If weak, auto-weblearn once, then re-check stable store
    if AUTO_WEBLEARN and _looks_low_quality(cleaned):
        # try learning
        await _run_brain(f"/weblearn {normalized}", timeout_s=max(25, int(req.timeout_s or 25)))
        stable2 = _find_stable_answer(topic_key)
        if stable2 and not _looks_low_quality(stable2):
            cfg = load_theme()
            themed2 = stable2 if "+++ VOX-CAST" in stable2 else apply_theme(stable2, topic=topic_key, cfg=cfg)
            return AskResponse(ok=True, topic=topic_key, answer=themed2, duration_s=float(raw_res.get("duration_s", time.time() - t0)), raw=(raw_res if req.raw else None), theme={"theme": cfg.theme, "intensity": cfg.intensity})

    # 7) Final return (whatever we got)
    cfg = load_theme()
    themed = cleaned if "+++ VOX-CAST" in cleaned else apply_theme(cleaned or normalized, topic=topic_key, cfg=cfg)

    return AskResponse(
        ok=True,
        topic=topic_key,
        answer=themed,
        duration_s=float(raw_res.get("duration_s", time.time() - t0)),
        error=None,
        raw=(raw_res if req.raw else None),
        theme={"theme": cfg.theme, "intensity": cfg.intensity},
    )
