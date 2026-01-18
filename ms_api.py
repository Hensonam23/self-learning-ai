#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import json
import os
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

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))
KNOWLEDGE_PATH = Path(os.environ.get("MS_KNOWLEDGE_PATH", str(REPO_DIR / "data" / "local_knowledge.json"))).resolve()

MS_API_KEY = (os.environ.get("MS_API_KEY", "") or "").strip()
AUTO_WEBLEARN = (os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() not in ("0", "false", "no", "off"))

app = FastAPI(title=APP_NAME, version=VERSION)

_LEARN_LOCK = asyncio.Lock()
_BRAIN_MOD = None

BAD_SOURCE_DOMAINS = {
    "indy100.com",
    "fandom.com",
    "wikia.com",
    "networklessons.com",
}


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
# Utilities
# ----------------------------
def _iso_now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


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


def _domain_from_url(u: str) -> str:
    try:
        from urllib.parse import urlparse
        d = urlparse(u).netloc.lower()
        if d.startswith("www."):
            d = d[4:]
        return d
    except Exception:
        return ""


def _normalize_topic(text: str) -> str:
    s = (text or "").strip()

    m = re.match(r'^\s*\{\s*"text"\s*:\s*"(.*?)"\s*\}\s*$', s)
    if m:
        s = (m.group(1) or "").strip()

    s = re.sub(r"^\s*(what is|what's|what are|define|explain)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s+about\s*$", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^\s*the\s+", "", s, flags=re.IGNORECASE).strip()

    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[?!.]+$", "", s).strip()
    return s


def _topic_key(topic: str) -> str:
    return _normalize_topic(topic).lower()


def _already_wrapped(answer: str) -> bool:
    a = answer or ""
    return ("+++ VOX-CAST" in a) and ("+++ END VOX +++" in a)


def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return True

    bad_phrases = [
        "all rights reserved",
        "close global navigation menu",
        "sign in / sign up",
        "captcha",
        "we apologize for the inconvenience",
        "your activity and behavior on this site made us think that you are a bot",
        "i do not have a taught answer for that yet",
        "i have also marked this topic for deeper research",
        "web search returned no results or could not be fetched",
        "i couldn't save a clean answer yet",
        "try asking with a little more detail",
        "refusing (junk topic)",
        "emotes icons photomode seasons skins styles utility updates",
    ]
    for p in bad_phrases:
        if p in a:
            return True

    # length gate (we will bypass this for taught_by_user entries)
    if len(a) < 80:
        return True

    return False


def _entry_has_bad_sources(entry: Dict[str, Any]) -> bool:
    if bool(entry.get("taught_by_user")):
        return False
    srcs = entry.get("sources") or []
    if not isinstance(srcs, list):
        return False
    for s in srcs:
        if not isinstance(s, str):
            continue
        d = _domain_from_url(s)
        if not d:
            continue
        if d in BAD_SOURCE_DOMAINS or d.endswith(".fandom.com"):
            return True
    return False


def _get_saved_entry(topic_k: str) -> Optional[Dict[str, Any]]:
    db = _read_json(KNOWLEDGE_PATH, {})
    if not isinstance(db, dict):
        return None
    entry = db.get(topic_k)
    if not isinstance(entry, dict):
        return None

    ans = (entry.get("answer") or "").strip()
    if not ans:
        return None

    # IMPORTANT FIX:
    # If the user taught/overrode it, trust it even if it's short (like "Aaron").
    if bool(entry.get("taught_by_user")):
        return entry

    # Otherwise apply quality filters for auto-learned stuff
    if _looks_low_quality(ans):
        return None
    if _entry_has_bad_sources(entry):
        return None

    return entry


def _store_entry(topic_k: str, answer: str, sources: Optional[List[str]], notes: str, confidence: float, taught_by_user: bool = False) -> None:
    db = _read_json(KNOWLEDGE_PATH, {})
    if not isinstance(db, dict):
        db = {}

    existing = db.get(topic_k)
    if not isinstance(existing, dict):
        existing = {}

    # Protect user overrides
    if bool(existing.get("taught_by_user")) and not taught_by_user:
        try:
            ex_conf = float(existing.get("confidence", 0.0) or 0.0)
        except Exception:
            ex_conf = 0.0
        if ex_conf >= 0.85 and (existing.get("answer") or "").strip():
            db[topic_k] = existing
            _write_json_atomic(KNOWLEDGE_PATH, db)
            return

    existing_sources = existing.get("sources")
    if not isinstance(existing_sources, list):
        existing_sources = []
    merged_sources: List[str] = []
    for s in (existing_sources + (sources or [])):
        s2 = (s or "").strip()
        if s2 and s2 not in merged_sources:
            merged_sources.append(s2)

    try:
        old_c = float(existing.get("confidence", 0.0) or 0.0)
    except Exception:
        old_c = 0.0

    entry = dict(existing)
    entry["answer"] = (answer or "").strip()
    entry["taught_by_user"] = bool(taught_by_user)
    entry["notes"] = notes
    entry["updated"] = _iso_now()
    entry["sources"] = merged_sources
    entry["confidence"] = max(old_c, float(confidence))

    db[topic_k] = entry
    _write_json_atomic(KNOWLEDGE_PATH, db)


def _wiki_search_title(q: str) -> Optional[str]:
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": q, "limit": 1, "namespace": 0, "format": "json"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return None
        titles = data[1]
        if isinstance(titles, list) and titles:
            t = (titles[0] or "").strip()
            return t or None
        return None
    except Exception:
        return None


def _wiki_summary(title: str) -> Optional[Tuple[str, str]]:
    try:
        safe = requests.utils.quote(title.replace(" ", "_"))
        r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        extract = (data.get("extract") or "").strip()
        page_url = ""
        cu = data.get("content_urls") or {}
        desk = cu.get("desktop") or {}
        page_url = (desk.get("page") or "").strip()
        if extract:
            return extract, page_url
        return None
    except Exception:
        return None


def _format_simple(topic: str, definition: str, url: str) -> str:
    t = (topic or "").strip()
    out: List[str] = []
    out.append(t.upper() if t else "ANSWER")
    out.append("")
    out.append("Definition:")
    out.append(f"- {definition.strip()}")
    out.append("")
    out.append("Sources:")
    out.append(f"- Wikipedia: {url}" if url else "- Wikipedia")
    return "\n".join(out).strip()


def _load_brain_module():
    global _BRAIN_MOD
    if _BRAIN_MOD is not None:
        return _BRAIN_MOD
    if not BRAIN_PATH.exists():
        raise RuntimeError(f"brain.py not found at {BRAIN_PATH}")

    spec = importlib.util.spec_from_file_location("machinespirit_brain", str(BRAIN_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load brain.py module spec")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    _BRAIN_MOD = mod
    return mod


def _brain_weblearn(topic: str, avoid_domains: List[str]) -> Optional[Tuple[str, List[str]]]:
    b = _load_brain_module()
    if not hasattr(b, "web_learn_topic"):
        return None

    ok, ans, sources, chosen_url = b.web_learn_topic(topic, forced_url="", avoid_domains=avoid_domains)  # type: ignore[misc]
    if not ok:
        return None

    ans_s = (ans or "").strip()
    if _looks_low_quality(ans_s):
        if chosen_url:
            d = _domain_from_url(chosen_url)
            if d and d not in avoid_domains:
                avoid_domains.append(d)
        return None

    srcs = [s for s in (sources or []) if isinstance(s, str)]
    return ans_s, srcs


def _is_probably_technical(topic: str) -> bool:
    t = (topic or "").lower()
    tech_words = [
        "rfc", "subnet", "cidr", "bgp", "dns", "dhcp", "tcp", "udp", "ip", "ipv4", "ipv6",
        "nat", "snat", "dnat", "arp", "icmp", "routing", "prefix", "asn", "autonomous system",
        "linux", "systemd", "docker", "nginx", "http", "https",
    ]
    return any(w in t for w in tech_words)


def _learn_and_store(topic_raw: str) -> bool:
    topic = _normalize_topic(topic_raw)
    k = _topic_key(topic)
    if not k:
        return False

    if _get_saved_entry(k) is not None:
        return True

    # Special: do NOT auto-web learn "my name"
    if k in ("my name", "what is my name", "name"):
        return False

    # Wikipedia-first for general topics
    if not _is_probably_technical(topic):
        title = _wiki_search_title(topic)
        if title:
            ws = _wiki_summary(title)
            if ws:
                extract, page_url = ws
                ans = _format_simple(title, extract, page_url)
                if not _looks_low_quality(ans):
                    _store_entry(
                        k,
                        ans,
                        sources=[page_url] if page_url else [],
                        notes="auto-learn (wikipedia-first)",
                        confidence=0.70,
                        taught_by_user=False,
                    )
                    return True

    # brain web learn fallback
    avoid = ["networklessons.com", "indy100.com", "fandom.com", "wikia.com", "fallout.fandom.com"]
    for _ in range(3):
        got = _brain_weblearn(topic, avoid)
        if got:
            ans2, srcs2 = got
            _store_entry(
                k,
                ans2,
                sources=srcs2,
                notes="auto-learn (brain web learn)",
                confidence=0.72,
                taught_by_user=False,
            )
            return True

    return False


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
        "knowledge_path": str(KNOWLEDGE_PATH),
        "auto_weblearn": AUTO_WEBLEARN,
        "theme": {"theme": cfg.theme, "intensity": cfg.intensity},
    }


@app.get("/theme")
async def get_theme(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    cfg = load_theme()
    choices = ui_intensity_choices()
    return {"ok": True, "theme": cfg.theme, "intensity": cfg.intensity, "choices": choices}


@app.post("/theme")
async def set_theme(request: Request, payload: ThemeRequest) -> Dict[str, Any]:
    _require_auth(request)
    cfg = save_theme(payload.theme, payload.intensity)
    return {"ok": True, "theme": cfg.theme, "intensity": cfg.intensity}


@app.post("/override")
async def override_answer(request: Request, payload: OverrideRequest) -> Dict[str, Any]:
    _require_auth(request)

    topic = _normalize_topic(payload.topic)
    k = _topic_key(topic)
    ans = (payload.answer or "").strip()

    if not k:
        raise HTTPException(status_code=422, detail="No topic to override")
    if not ans:
        raise HTTPException(status_code=422, detail="Missing answer")

    _store_entry(
        k,
        ans,
        sources=[],
        notes="override via UI/API conversation",
        confidence=0.90,
        taught_by_user=True,
    )
    return {"ok": True, "topic": k}


@app.post("/ask", response_model=AskResponse)
async def ask(request: Request, req: AskRequest) -> AskResponse:
    _require_auth(request)
    t0 = time.time()

    raw_text = (req.text or "").strip()
    if not raw_text:
        raise HTTPException(status_code=422, detail="text is required")

    topic = _normalize_topic(raw_text)
    k = _topic_key(topic)
    if not k:
        raise HTTPException(status_code=422, detail="Could not normalize topic")

    # 1) Saved answer first
    entry = _get_saved_entry(k)
    if entry:
        ans = (entry.get("answer") or "").strip()

        # Make identity answers nicer if they are short like "Aaron"
        if k == "my name" and len(ans) <= 40 and "\n" not in ans:
            ans = f"Your name is {ans}."

        cfg = load_theme()
        themed = ans if _already_wrapped(ans) else apply_theme(ans, topic=topic, cfg=cfg)
        return AskResponse(
            ok=True,
            topic=k,
            answer=themed,
            duration_s=time.time() - t0,
            error=None,
            raw=None,
            theme={"theme": cfg.theme, "intensity": cfg.intensity, "saved": True},
        )

    # Special: name questions (only if not taught yet)
    if k in ("my name", "what is my name", "name"):
        cfgm = load_theme()
        msg = "I don’t know your name yet. Tell me: “my name is Aaron” (or whatever you want), and I’ll remember it."
        themedm = apply_theme(msg, topic=topic, cfg=cfgm)
        return AskResponse(
            ok=True,
            topic="my name",
            answer=themedm,
            duration_s=time.time() - t0,
            error=None,
            raw=None,
            theme={"theme": cfgm.theme, "intensity": cfgm.intensity},
        )

    # 2) Auto-learn if enabled
    if AUTO_WEBLEARN:
        async with _LEARN_LOCK:
            await asyncio.to_thread(_learn_and_store, topic)

        entry2 = _get_saved_entry(k)
        if entry2:
            ans2 = (entry2.get("answer") or "").strip()
            cfg2 = load_theme()
            themed2 = ans2 if _already_wrapped(ans2) else apply_theme(ans2, topic=topic, cfg=cfg2)
            return AskResponse(
                ok=True,
                topic=k,
                answer=themed2,
                duration_s=time.time() - t0,
                error=None,
                raw=None,
                theme={"theme": cfg2.theme, "intensity": cfg2.intensity, "learned": True},
            )

    # 3) Fallback
    cfg3 = load_theme()
    fallback = (
        f"{topic}\n\n"
        "I couldn't save a clean answer yet.\n\n"
        "Try asking with a little more detail (example: add 'video game' or 'computer hardware')."
    )
    themed3 = apply_theme(fallback, topic=topic, cfg=cfg3)
    return AskResponse(
        ok=True,
        topic=k,
        answer=themed3,
        duration_s=time.time() - t0,
        error=None,
        raw=None,
        theme={"theme": cfg3.theme, "intensity": cfg3.intensity},
    )
