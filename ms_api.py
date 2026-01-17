#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ms_theme import apply_theme, load_theme, save_theme, ui_intensity_choices


APP_NAME = "MachineSpirit API"
VERSION = "0.3.10"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")
MS_API_KEY = os.environ.get("MS_API_KEY", "")

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))
KNOWLEDGE_PATH = Path(os.environ.get("MS_KNOWLEDGE_PATH", str(REPO_DIR / "data" / "local_knowledge.json")))

AUTO_RESEARCH = os.environ.get("MS_AUTO_RESEARCH", "1").strip().lower() not in ("0", "false", "no", "off")
AUTO_RESEARCH_TRIES = int(os.environ.get("MS_AUTO_RESEARCH_TRIES", "2"))  # 0=off, 1=weblearn, 2=weblearn+wiki


app = FastAPI(title=APP_NAME, version=VERSION)


# ----------------------------
# Models
# ----------------------------
class AskRequest(BaseModel):
    text: str
    timeout_s: Optional[int] = 30
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
    confidence: Optional[float] = 0.95
    sources: Optional[List[str]] = None


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
# Topic normalization (prevents fallout 3 vs fallout 3?)
# ----------------------------
def _normalize_topic(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s+", " ", s).strip()
    # strip trailing punctuation that creates duplicate topics
    s = re.sub(r"[?!.:;,\s]+$", "", s).strip()
    return s


def _topic_key(topic: str) -> str:
    k = (topic or "").strip().lower()
    k = re.sub(r"\s+", " ", k).strip()
    k = re.sub(r"[?!.:;,\s]+$", "", k).strip()
    return k


# ----------------------------
# Knowledge read/write (direct)
# ----------------------------
def _read_knowledge() -> Dict[str, Any]:
    if not KNOWLEDGE_PATH.exists():
        return {}
    try:
        data = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8", errors="replace") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_knowledge(data: Dict[str, Any]) -> None:
    KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KNOWLEDGE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(KNOWLEDGE_PATH)


def _store_override(topic: str, answer: str, confidence: float = 0.95, sources: Optional[List[str]] = None) -> None:
    tkey = _topic_key(topic)
    data = _read_knowledge()
    entry = data.get(tkey) if isinstance(data.get(tkey), dict) else {}

    entry["answer"] = (answer or "").strip()
    entry["confidence"] = float(max(float(entry.get("confidence") or 0.0), confidence))
    entry["taught_by_user"] = True  # prevents weaker web learns overwriting it
    entry["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    entry["sources"] = list(dict.fromkeys([s for s in (sources or []) if isinstance(s, str) and s.strip()]))

    data[tkey] = entry
    _write_knowledge(data)


# ----------------------------
# Brain helpers
# ----------------------------
def _brain_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]


_PROMPT_RE = re.compile(r"^\s*>\s*(.*)$")


def _extract_last_answer(stdout: str) -> str:
    if not stdout:
        return ""
    sections: List[Tuple[str, List[str]]] = []
    prompt = ""
    lines: List[str] = []
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("Machine Spirit brain online."):
            continue
        m = _PROMPT_RE.match(line)
        if m:
            if prompt or lines:
                sections.append((prompt, lines))
            prompt = (m.group(1) or "").strip()
            lines = []
            continue
        lines.append(line.rstrip())
    if prompt or lines:
        sections.append((prompt, lines))

    for p, body in reversed(sections):
        p = (p or "").strip()
        if not p or p.lower().startswith("shutting down") or p.startswith("/"):
            continue
        text = "\n".join(body).strip()
        text = re.sub(r"^\n+", "", text).strip()
        if text:
            return f"{p}\n\n{text}".strip()
        return p.strip()

    return stdout.strip()


async def _run_brain(stdin_text: str, timeout_s: int) -> Dict[str, Any]:
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
        out_b, err_b = await asyncio.wait_for(proc.communicate(input=stdin_text.encode("utf-8")), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="brain.py timed out")

    return {
        "duration_s": time.time() - t0,
        "stdout": (out_b or b"").decode("utf-8", errors="replace"),
        "stderr": (err_b or b"").decode("utf-8", errors="replace"),
        "exit_code": int(proc.returncode or 0),
        "args": _brain_args(),
    }


# ----------------------------
# Quality filtering (prevents fandom/rumor pages being accepted)
# ----------------------------
def _extract_source_urls(answer: str) -> List[str]:
    if not answer:
        return []
    urls: List[str] = []
    in_sources = False
    for line in answer.splitlines():
        s = line.strip()
        if s.lower().startswith("sources:"):
            in_sources = True
            continue
        if in_sources:
            m = re.search(r"(https?://\S+)", s)
            if m:
                urls.append(m.group(1).strip())
    return urls


def _is_bad_source_url(url: str) -> bool:
    u = (url or "").lower()
    # fandom/wikia are not good "definition" sources (too much nav/noise)
    if "fandom.com" in u or "wikia.com" in u:
        return True
    # rumor/news/leak style sources are bad for "what is X" definitions
    if any(x in u for x in ("gaming", "leak", "rumor", "rumour")):
        return True
    return False


def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip()
    if not a:
        return True

    al = a.lower()
    junk_markers = [
        "all rights reserved",
        "close global navigation menu",
        "we apologize for the inconvenience",
        "made us think that you are a bot",
        "captcha",
        "log in",
        "sign up",
        "service status",
        "emotes icons photomode seasons skins",  # fallout fandom nav garbage
        "i tried researching that, but the sources i found were too low-quality",
    ]
    if any(m in al for m in junk_markers):
        return True

    urls = _extract_source_urls(a)
    if any(_is_bad_source_url(u) for u in urls):
        return True

    # rumor/leak content masquerading as definition
    rumor_words = ["remaster", "remake", "planned release", "insider", "leak", "rumor", "rumour"]
    if "definition:" in al and any(w in al for w in rumor_words):
        return True

    return False


# ----------------------------
# Wikipedia fallback (clean)
# ----------------------------
def _wiki_summary(topic: str) -> Optional[Tuple[str, str]]:
    q = (topic or "").strip()
    if not q:
        return None
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": q, "limit": 1, "namespace": 0, "format": "json"},
            timeout=12,
            headers={"User-Agent": "MachineSpirit/0.3.10"},
        )
        r.raise_for_status()
        data = r.json()
        title = (data[1][0] if isinstance(data, list) and len(data) > 1 and data[1] else "") or ""
        if not title:
            return None

        safe = requests.utils.quote(title.replace(" ", "_"))
        rs = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}",
            timeout=12,
            headers={"User-Agent": "MachineSpirit/0.3.10"},
        )
        rs.raise_for_status()
        js = rs.json()
        extract = (js.get("extract") or "").strip()
        page_url = ""
        if isinstance(js.get("content_urls"), dict):
            page_url = ((js["content_urls"].get("desktop") or {}).get("page") or "").strip()

        if not extract:
            return None

        out = []
        out.append(topic.upper())
        out.append("")
        out.append("Definition:")
        out.append(f"- {extract}")
        out.append("")
        out.append("Sources:")
        out.append(f"- Wikipedia: {page_url}" if page_url else f"- Wikipedia: {title}")
        return ("\n".join(out).strip(), page_url or f"https://en.wikipedia.org/wiki/{safe}")
    except Exception:
        return None


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
async def root() -> Dict[str, Any]:
    return {"ok": True, "app": APP_NAME, "version": VERSION, "ui_hint": "UI runs on port 8020."}


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
        "auto_research": AUTO_RESEARCH,
        "tries": AUTO_RESEARCH_TRIES,
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
async def override_answer(request: Request, payload: OverrideRequest) -> Dict[str, Any]:
    _require_auth(request)
    t = _normalize_topic(payload.topic)
    a = (payload.answer or "").strip()
    if not t or not a:
        raise HTTPException(status_code=422, detail="topic and answer are required")
    _store_override(t, a, confidence=float(payload.confidence or 0.95), sources=payload.sources or [])
    return {"ok": True, "topic": _topic_key(t)}


@app.post("/ask", response_model=AskResponse)
async def ask(request: Request, req: AskRequest) -> AskResponse:
    _require_auth(request)
    t0 = time.time()

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")

    # theme command passthrough (same behavior as before)
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
                raise HTTPException(status_code=422, detail="Theme name is required")
            cfg = save_theme(theme_name, intensity)
            return AskResponse(ok=True, topic="theme", answer=f"Theme set to: {cfg.theme} ({cfg.intensity}).", duration_s=time.time() - t0, theme={"theme": cfg.theme, "intensity": cfg.intensity})

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    topic = _normalize_topic(text)
    key = _topic_key(topic)

    raw1 = await _run_brain(topic + "\n", timeout_s=int(req.timeout_s or 30))
    ans1 = _extract_last_answer(raw1.get("stdout", ""))

    final = ans1
    used_wiki = False

    # auto-research if low quality
    if AUTO_RESEARCH and _looks_low_quality(final) and AUTO_RESEARCH_TRIES >= 1:
        raw2 = await _run_brain(f"/weblearn {topic}\n{topic}\n", timeout_s=int((req.timeout_s or 30) + 25))
        ans2 = _extract_last_answer(raw2.get("stdout", ""))
        if ans2 and not _looks_low_quality(ans2):
            final = ans2
        else:
            final = ans2 or final

    # hard fallback: wiki summary (especially for games/media)
    if AUTO_RESEARCH and _looks_low_quality(final) and AUTO_RESEARCH_TRIES >= 2:
        ws = _wiki_summary(topic)
        if ws:
            wiki_text, wiki_url = ws
            final = wiki_text
            used_wiki = True
            # store as taught_by_user so it never flips back to rumor/fandom later
            _store_override(topic, wiki_text, confidence=0.92, sources=[wiki_url])

    cfg = load_theme()
    themed = apply_theme(final.strip(), topic=topic, cfg=cfg)

    return AskResponse(
        ok=True,
        topic=key,
        answer=themed,
        duration_s=float(raw1.get("duration_s", time.time() - t0)),
        error=None,
        raw=(raw1 if req.raw else None),
        theme={"theme": cfg.theme, "intensity": cfg.intensity, "wiki_curated": used_wiki},
    )
