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


# --- MS_PRIVATE_PROFILE_V1: local-only sensitive memory (NEVER commit/export) ---
# Stores personal info locally in: data/private_profile.json
from pathlib import Path as _MS_PP_Path
import json as _MS_PP_json
import os as _MS_PP_os

_MS_PRIVATE_PROFILE_PATH = (_MS_PP_Path(__file__).resolve().parent / "data" / "private_profile.json")

def _pp_load() -> dict:
    try:
        if _MS_PRIVATE_PROFILE_PATH.exists():
            obj = _MS_PP_json.loads(_MS_PRIVATE_PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
    except Exception:
        pass
    return {"user_name": None, "email": None, "phone": None, "address": None}

def _pp_save(obj: dict) -> bool:
    try:
        _MS_PRIVATE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MS_PRIVATE_PROFILE_PATH.with_suffix(".tmp")
        tmp.write_text(_MS_PP_json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _MS_PP_os.replace(tmp, _MS_PRIVATE_PROFILE_PATH)
        return True
    except Exception:
        return False

def _pp_get_name():
    nm = _pp_get_field("user_name")
    return nm if nm else None

def _pp_set_name(nm: str) -> bool:
    nm = (nm or "").strip()
    if not nm:
        return False
    return _pp_set_field("user_name", nm)

def _pp_get_field(key: str):
    try:
        obj = _pp_load()
        val = obj.get(key, None)
        if isinstance(val, str):
            val = val.strip()
            return val if val else None
        return val
    except Exception:
        return None

def _pp_set_field(key: str, val) -> bool:
    try:
        if not key:
            return False
        obj = _pp_load()
        obj[key] = val
        return _pp_save(obj)
    except Exception:
        return False

def _pp_forget_field(key: str) -> bool:
    try:
        obj = _pp_load()
        if key in obj:
            obj[key] = None
            return _pp_save(obj)
        return True
    except Exception:
        return False
# --- end MS_PRIVATE_PROFILE_V1 ---


app = FastAPI(title=APP_NAME, version=VERSION)

# =========================
# FASTLEARN_V1_BEGIN
# =========================
import os as _os
import re as _re
import json as _json
import datetime as _dt
import sys as _sys
import time as _time
import subprocess as _subprocess
from pathlib import Path as _Path

_MS_FASTLEARN_ENABLED = (_os.environ.get("MS_FASTLEARN_ENABLED", "1").strip() == "1")
_MS_FASTLEARN_CONF_THRESHOLD = float(_os.environ.get("MS_FASTLEARN_CONF_THRESHOLD", "0.70"))
_MS_FASTLEARN_WINDOW_S = int(_os.environ.get("MS_FASTLEARN_WINDOW_S", "60"))
_MS_FASTLEARN_MAX_PER_WINDOW = int(_os.environ.get("MS_FASTLEARN_MAX_PER_WINDOW", "3"))
_MS_FASTLEARN_WEBQUEUE_TIMEOUT_S = int(_os.environ.get("MS_FASTLEARN_WEBQUEUE_TIMEOUT_S", "25"))
_MS_FASTLEARN_FLOCK_WAIT_S = int(_os.environ.get("MS_FASTLEARN_FLOCK_WAIT_S", "3"))

_FASTLEARN_EVENTS = []  # timestamps (seconds)

_REPO_DIR_FL = _Path(_os.environ.get("MS_REPO_DIR", str(_Path(__file__).resolve().parent))).resolve()
_KNOWLEDGE_PATH_FL = _Path(_os.environ.get("MS_KNOWLEDGE_PATH", str(_REPO_DIR_FL / "data" / "local_knowledge.json"))).resolve()
_QUEUE_PATH_FL = _REPO_DIR_FL / "data" / "research_queue.json"
_LOCK_PATH_FL = _REPO_DIR_FL / ".machinespirit.lock"
_BRAIN_PATH_FL = _REPO_DIR_FL / "brain.py"

def _fl_norm_text(text: str) -> str:
    t = (text or "").strip()
    t = _re.sub(r"^\s*(what is|what's|what are|define|explain)\s+", "", t, flags=_re.IGNORECASE).strip()
    t = _re.sub(r"[?!.]+$", "", t).strip()
    t = _re.sub(r"\s+", " ", t).strip()
    return t.lower()

def _fl_looks_junky(topic: str) -> bool:
    if not topic:
        return True
    if len(topic) > 80:
        return True
    if "http://" in topic or "https://" in topic or "www." in topic:
        return True
    if "/" in topic or "\\" in topic:
        return True
    if _re.search(r"\b(sudo|rm\s+-rf|chmod\s+777|curl\s+|wget\s+|ssh\s+)\b", topic):
        return True
    if _re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", topic):
        return True
    return False

def _fl_rate_limited() -> bool:
    now = _time.time()
    while _FASTLEARN_EVENTS and (now - _FASTLEARN_EVENTS[0]) > _MS_FASTLEARN_WINDOW_S:
        _FASTLEARN_EVENTS.pop(0)
    return len(_FASTLEARN_EVENTS) >= _MS_FASTLEARN_MAX_PER_WINDOW

def _fl_load_local(topic_norm: str):
    try:
        if not _KNOWLEDGE_PATH_FL.exists():
            return None
        db = _json.loads(_KNOWLEDGE_PATH_FL.read_text(encoding="utf-8", errors="replace") or "{}")
        if not isinstance(db, dict):
            return None
        ent = db.get(topic_norm)
        return ent if isinstance(ent, dict) else None
    except Exception:
        return None

def _fl_enqueue(topic_norm: str, current_conf: float):
    today = _dt.date.today().isoformat()
    item = {
        "topic": topic_norm,
        "reason": "User asked (fastlearn)",
        "requested_on": today,
        "status": "pending",
        "current_confidence": float(current_conf or 0.0),
    }

    _QUEUE_PATH_FL.parent.mkdir(parents=True, exist_ok=True)

    try:
        q = _json.loads(_QUEUE_PATH_FL.read_text(encoding="utf-8", errors="replace") or "[]")
        if not isinstance(q, list):
            q = []
    except Exception:
        q = []

    for e in q:
        if isinstance(e, dict) and (e.get("topic") == topic_norm):
            return False

    q.append(item)

    tmp = _QUEUE_PATH_FL.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(q, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _os.replace(tmp, _QUEUE_PATH_FL)
    return True

def _fl_run_webqueue_once():
    if not _BRAIN_PATH_FL.exists():
        print("fastlearn: brain.py not found, cannot run webqueue")
        return False

    cmd = [
        "/usr/bin/flock", "-w", str(_MS_FASTLEARN_FLOCK_WAIT_S),
        str(_LOCK_PATH_FL),
        _sys.executable, str(_BRAIN_PATH_FL),
        "--webqueue", "--limit", "1",
    ]

    try:
        r = _subprocess.run(
            cmd,
            cwd=str(_REPO_DIR_FL),
            capture_output=True,
            text=True,
            timeout=_MS_FASTLEARN_WEBQUEUE_TIMEOUT_S,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if out:
            print("fastlearn: webqueue stdout:", out[-500:])
        if err:
            print("fastlearn: webqueue stderr:", err[-500:])
        return (r.returncode == 0)
    except _subprocess.TimeoutExpired:
        print("fastlearn: webqueue timed out")
        return False
    except Exception as e:
        print("fastlearn: webqueue failed:", type(e).__name__, str(e))
        return False

def _fastlearn_try(text: str):
    if not _MS_FASTLEARN_ENABLED:
        return
    topic = _fl_norm_text(text)
    if _fl_looks_junky(topic):
        return

    ent = _fl_load_local(topic)
    conf = 0.0
    if ent:
        try:
            conf = float(ent.get("confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        if ent.get("answer") and conf >= _MS_FASTLEARN_CONF_THRESHOLD:
            return

    queued = _fl_enqueue(topic, conf)

    if _fl_rate_limited():
        if queued:
            print("fastlearn: rate-limited, queued topic=%r conf=%.2f" % (topic, conf))
        return

    if queued:
        print("fastlearn: queued topic=%r conf=%.2f" % (topic, conf))

    _FASTLEARN_EVENTS.append(_time.time())

    ran = _fl_run_webqueue_once()
    if ran:
        print("fastlearn: webqueue attempted for topic=%r" % (topic,))
# =========================
# FASTLEARN_V1_END
# =========================

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

# =========================
# SMALLTALK_HELPERS_SAFE_V1
# =========================
import re as _re_smalltalk

_SMALLTALK_SET = {
    "hi","hello","hey","yo","sup","hiya","howdy",
    "thanks","thank you","thx",
    "ok","okay","k","cool","nice",
    "good morning","good afternoon","good evening",
    "goodnight","good night",
    "how are you","how r u","hru","how you doing","how's it going","hows it going",
}

def _is_smalltalk_msg(text: str) -> bool:
    t = (text or "").strip().lower()
    t = _re_smalltalk.sub(r"\s+", " ", t).strip()
    if not t:
        return True
    if t in _SMALLTALK_SET:
        return True
    if len(t) <= 3 and t in {"yo","k","ok","kk"}:
        return True
    return False

def _smalltalk_reply(text: str) -> str:
    t = (text or "").strip().lower()
    if t in {"hi","hello","hey","yo","hiya","howdy","sup"}:
        return "Hey 🙂 Ask me something like: 'what is VLAN' or 'explain NAT'."
    if "how are you" in t or "how's it going" in t or "hows it going" in t:
        return "Doing good. What do you want to learn or build today?"
    if "thank" in t or t == "thx":
        return "No problem."
    if t in {"ok","okay","k","cool","nice"}:
        return "👍"
    return "Hey. Ask me a question and I’ll try to answer it."

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




# ============================================================
# MS_UPGRADE_LOCAL_FACTS_V3_AND_PINNED_V1
# - Local facts (time/date/day + name questions) never use web
# - Pinned answers (taught_by_user or confidence>=0.90) always win
# ============================================================

def _ms_norm_topic_v1(s: str) -> str:
    import re as _re
    t = (s or "").strip().lower()
    # remove json wrapper if someone pastes {"text":"..."}
    m = _re.match(r'^\s*\{\s*"text"\s*:\s*"(.+)"\s*\}\s*$', t)
    if m:
        t = m.group(1).strip().lower()

    t = _re.sub(r"^\s*(what is|what's|define|explain)\s+", "", t, flags=_re.IGNORECASE).strip()
    t = _re.sub(r"[?!.]+$", "", t).strip()
    return t

def _ms_read_knowledge_db_v1():
    try:
        if not KNOWLEDGE_PATH.exists():
            return {}
        raw = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8", errors="replace") or "{}")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}

def _ms_get_entry_v1(topic_n: str):
    if not topic_n:
        return None
    db = _ms_read_knowledge_db_v1()
    ent = db.get(topic_n)
    return ent if isinstance(ent, dict) else None

def _ms_is_pinned_v1(ent: dict) -> bool:
    try:
        if ent.get("taught_by_user") is True:
            return True
        c = float(ent.get("confidence", 0.0) or 0.0)
        return c >= 0.90
    except Exception:
        return False

def _local_facts_answer_v3(text: str):
    """
    Returns (topic, answer) or None.
    Covers:
      - time/date/day (local)
      - what is my name / what's my name
      - what is your name / what's your name
    """
    import datetime as _dt
    import re as _re
    import time as _time

    raw = (text or "").strip()
    if not raw:
        return None

    s0 = raw.lower().strip()
    s0 = _re.sub(r"[?!.]+$", "", s0).strip()
    s1 = _re.sub(r"^\s*(what is|what's|define|explain)\s+", "", s0, flags=_re.IGNORECASE).strip()
    cand = {s0, s1}

    # local time with tz label if possible
    try:
        now = _dt.datetime.now().astimezone()
        tzname = now.tzname() or "local"
    except Exception:
        now = _dt.datetime.now()
        tzname = (_time.tzname[0] if getattr(_time, "tzname", None) else "local") or "local"

    # --- NAME: user ---
    if any(x in cand for x in ("my name", "what is my name", "what's my name", "who am i")):
        ent = _ms_get_entry_v1("my name")
        ans = (ent.get("answer") or "").strip() if isinstance(ent, dict) else ""
        if ans:
            return ("my name", ans)
        return ("my name", 'I don’t know your name yet. Tell me: "my name is <your name>" and I’ll remember it.')

    # --- NAME: bot ---
    if any(x in cand for x in ("your name", "what is your name", "what's your name")):
        ent = _ms_get_entry_v1("your name")
        ans = (ent.get("answer") or "").strip() if isinstance(ent, dict) else ""
        return ("your name", ans or "Machine Spirit")

    # --- TIME ---
    if any(x in cand for x in ("time", "the time", "what time is it", "what is the time", "current time", "time now")):
        hhmm = now.strftime("%I:%M %p").lstrip("0")
        return ("time", f"{hhmm} ({tzname})")

    # --- DATE ---
    if any(x in cand for x in ("date", "the date", "what is the date", "what's the date", "todays date", "today's date", "current date", "date today")):
        out = now.strftime("%A, %B %d, %Y").replace(" 0", " ")
        return ("date", out)

    # --- DAY ---
    if any(x in cand for x in ("day", "what day is it", "what day is it today", "day of week")):
        return ("day", now.strftime("%A"))

    return None
@app.post("/ask", response_model=AskResponse)
async def ask(request: Request, req: AskRequest) -> AskResponse:
    # FASTLEARN_CALL_IN_ASK
    try:
        _fl_text = ""
        if "payload" in locals():
            _fl_text = getattr(payload, "text", "") or ""
        # =========================
        # SMALLTALK_BYPASS_SAFE_V1
        # =========================
        _txt = (getattr(req, 'text', '') or '').strip()
        if _is_smalltalk_msg(_txt):
            _ans = _smalltalk_reply(_txt)
            return {
                'ok': True,
                'topic': 'chat',
                'answer': _ans,
                'duration_s': 0.0,
                'error': None,
                'raw': None,
                'theme': {'theme': 'none', 'intensity': 'light'},
            }

        if not _fl_text:
            for _v in locals().values():
                _t = getattr(_v, "text", "") if hasattr(_v, "text") else ""
                if isinstance(_t, str) and _t.strip():
                    _fl_text = _t
                    break
        _fastlearn_try(_fl_text)
    except Exception:
        pass
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
    lf = _local_facts_answer_v3(text)
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

    # --- PINNED_ANSWERS_ALWAYS_WIN_V1 ---
    # If the normalized topic already has a user-taught / high-confidence answer, return it.
    topic_n = _ms_norm_topic_v1(text)
    ent = _ms_get_entry_v1(topic_n)
    if isinstance(ent, dict) and _ms_is_pinned_v1(ent) and (ent.get("answer") or "").strip():
        cfg = load_theme()
        themed = apply_theme((ent.get("answer") or "").strip(), topic=topic_n, cfg=cfg)
        return AskResponse(ok=True, topic=topic_n, answer=themed, duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})


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
