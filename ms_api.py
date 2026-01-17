#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ms_theme import apply_theme, load_theme, save_theme, ui_intensity_choices

APP_NAME = "MachineSpirit API"
VERSION = "0.6.1"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")
MS_API_KEY = os.environ.get("MS_API_KEY", "")

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))

# auto-improve weak answers
AUTO_WEBLEARN = os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() in ("1", "true", "yes", "on")
AUTO_WEBLEARN_TIMEOUT_S = int(os.environ.get("MS_AUTO_WEBLEARN_TIMEOUT_S", "90"))
AUTO_WEBLEARN_MAX_ATTEMPTS = int(os.environ.get("MS_AUTO_WEBLEARN_MAX_ATTEMPTS", "3"))

# wikipedia definitional fallback (especially for games/media)
AUTO_WIKI_FALLBACK = os.environ.get("MS_AUTO_WIKI_FALLBACK", "1").strip().lower() in ("1", "true", "yes", "on")

CFG_DIR = Path(os.path.expanduser("~/.config/machinespirit"))
OVERRIDES_PATH = Path(os.environ.get("MS_OVERRIDES_PATH", str(CFG_DIR / "overrides.json"))).expanduser()

# avoid saving these as "definitions"
DEFAULT_AVOID_DOMAINS = [
    "fandom.com", "wikia.com",
    "reddit.com", "quora.com",
    "steamcommunity.com",
    "indy100.com",  # rumor/leak/news pages kept poisoning definitions
]

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
    used_override: Optional[bool] = None
    saved_override: Optional[bool] = None


class ThemeRequest(BaseModel):
    theme: str
    intensity: str


def _require_auth(request: Request) -> None:
    if not MS_API_KEY:
        raise HTTPException(status_code=500, detail="MS_API_KEY is not set on server")
    key = request.headers.get("x-api-key", "")
    if key != MS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _brain_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]


def _is_definition_query(original: str) -> bool:
    s = (original or "").strip().lower()
    return bool(re.match(r"^(what\s+is|what's|define|explain)\b", s))


def _normalize_topic(text: str) -> str:
    s = (text or "").strip()
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", s, flags=re.IGNORECASE).strip()
    s = s.strip().strip('"\'')

    # strip trailing punctuation like "fallout 3?" -> "fallout 3"
    s = re.sub(r"[?!\.]+$", "", s).strip()
    return s


def _topic_key(topic: str) -> str:
    return _normalize_topic(topic).lower().strip()


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


def _extract_domains_from_text(text: str) -> List[str]:
    if not text:
        return []
    domains: List[str] = []
    for m in re.finditer(r"https?://([A-Za-z0-9\.\-]+)", text):
        d = (m.group(1) or "").lower().strip().lstrip(".")
        if d and d not in domains:
            domains.append(d)
    return domains


def _domain_is_lowtrust(d: str) -> bool:
    d = (d or "").lower()
    return any(x in d for x in DEFAULT_AVOID_DOMAINS)


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
    return hits >= 8


def _definition_line(answer: str) -> str:
    """
    Pull the first definition bullet line after 'Definition:' if present.
    """
    if not answer:
        return ""
    m = re.search(r"(?im)^\s*definition\s*:\s*$", answer)
    if not m:
        return ""
    tail = answer[m.end():]
    for line in tail.splitlines():
        s = line.strip()
        if not s:
            continue
        # common format "- blah"
        if s.startswith("-"):
            return s.lstrip("-").strip()
        return s
    return ""


def _looks_like_rumor_news(def_line: str) -> bool:
    t = (def_line or "").lower()
    rumor_markers = [
        "planned release",
        "insider",
        "leak",
        "rumor",
        "claims",
        "reveal window",
        "twitter",
        "x / twitter",
        "subreddit",
        "responding to a post",
        "reportedly",
        "countdown",
        "remaster",
        "remake",
    ]
    return any(m in t for m in rumor_markers)


def _topic_kind(topic: str) -> str:
    t = (topic or "").lower()
    entertainment = [
        "fallout", "skyrim", "game", "video game", "movie", "tv", "series",
        "anime", "song", "album", "band", "book", "novel",
    ]
    tech = [
        "bgp", "ospf", "tcp", "udp", "dns", "dhcp", "nat", "subnet", "cidr",
        "routing", "icmp", "ipv4", "ipv6", "asn", "autonomous system",
    ]
    if any(k in t for k in entertainment):
        return "entertainment"
    if any(k in t for k in tech):
        return "tech"
    return "general"


def _looks_low_quality(answer: str, kind: str, is_def_q: bool) -> bool:
    a = (answer or "").strip()
    al = a.lower()

    if not a:
        return True

    min_len = 80 if kind == "entertainment" else 130
    if len(a) < min_len:
        return True

    if "refusing (junk topic)" in al:
        return True
    if "i tried researching that" in al:
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

    # extra: definitional queries must look like an actual definition, not rumors/news
    if is_def_q:
        dl = _definition_line(a)
        if dl:
            # must include an "is a / is an / is the" style definition
            has_is = (" is a " in dl.lower()) or (" is an " in dl.lower()) or (" is the " in dl.lower())
            if not has_is:
                return True
            if _looks_like_rumor_news(dl):
                return True

    return False


def _http_get_json(url: str, timeout_s: int = 12) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "MachineSpirit/0.6.1 (+local)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8", errors="replace"))


def _wiki_summary(query: str) -> Optional[Dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return None

    try:
        api = "https://en.wikipedia.org/w/api.php"
        params = {"action": "opensearch", "search": q, "limit": "1", "namespace": "0", "format": "json"}
        url = api + "?" + urllib.parse.urlencode(params)
        data = _http_get_json(url, timeout_s=12)

        if not (isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list) and data[1]):
            return None

        title = (data[1][0] or "").strip()
        if not title:
            return None

        safe = urllib.parse.quote(title.replace(" ", "_"))
        s_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}"
        summary = _http_get_json(s_url, timeout_s=12)

        extract = (summary.get("extract") or "").strip() if isinstance(summary, dict) else ""
        page_url = ""
        if isinstance(summary, dict):
            cu = summary.get("content_urls") or {}
            desktop = cu.get("desktop") or {}
            page_url = (desktop.get("page") or "").strip()

        if not extract:
            return None

        return {"title": title, "extract": extract, "url": page_url or f"https://en.wikipedia.org/wiki/{safe}"}
    except Exception:
        return None


async def _run_brain(one_line: str, timeout_s: int) -> Dict[str, Any]:
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
        "args": _brain_args(),
        "input": one_line,
    }


def _load_overrides() -> Dict[str, Any]:
    try:
        if not OVERRIDES_PATH.exists():
            return {}
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_overrides(d: Dict[str, Any]) -> None:
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(str(tmp), 0o600)
    tmp.replace(OVERRIDES_PATH)
    try:
        os.chmod(str(OVERRIDES_PATH), 0o600)
    except Exception:
        pass


def _save_auto_override(topic: str, answer: str, sources: List[str]) -> bool:
    key = _topic_key(topic)
    if not key or not answer.strip():
        return False

    overrides = _load_overrides()
    existing = overrides.get(key)
    if isinstance(existing, dict) and existing.get("source") == "user_override":
        return False

    overrides[key] = {
        "topic": _normalize_topic(topic),
        "answer": answer.strip(),
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "auto_research",
        "confidence": 0.78,
        "sources": sources[:6],
    }
    _save_overrides(overrides)
    return True


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
        "auto": {
            "weblearn": AUTO_WEBLEARN,
            "weblearn_attempts": AUTO_WEBLEARN_MAX_ATTEMPTS,
            "wiki_fallback": AUTO_WIKI_FALLBACK,
        },
        "overrides_path": str(OVERRIDES_PATH),
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

    original_text = (req.text or "").strip()
    if not original_text:
        raise HTTPException(status_code=422, detail="text is required")

    # Theme commands (kept)
    if original_text.lower().startswith("/theme"):
        parts = original_text.split()
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
            themed = apply_theme(msg.strip(), topic="theme", cfg=cfg)
            return AskResponse(ok=True, topic="theme", answer=themed, duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False, used_override=False, saved_override=False)

        if len(parts) >= 2 and parts[1].lower() in ("off", "none", "disable", "disabled"):
            cfg = save_theme("none", "light")
            themed = apply_theme("Theme disabled.", topic="theme", cfg=cfg)
            return AskResponse(ok=True, topic="theme", answer=themed, duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False, used_override=False, saved_override=False)

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
            themed = apply_theme(f"Theme set to: {cfg.theme} ({cfg.intensity}).", topic="theme", cfg=cfg)
            return AskResponse(ok=True, topic="theme", answer=themed, duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False, used_override=False, saved_override=False)

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    is_def_q = _is_definition_query(original_text)

    normalized = _normalize_topic(original_text)
    key = _topic_key(normalized)
    kind = _topic_kind(normalized)

    # Override wins immediately
    overrides = _load_overrides()
    if key in overrides and isinstance(overrides[key], dict) and overrides[key].get("answer"):
        cfg = load_theme()
        themed = apply_theme(overrides[key]["answer"], topic=normalized, cfg=cfg)
        return AskResponse(ok=True, topic=key, answer=themed, duration_s=time.time() - t0,
                           theme={"theme": cfg.theme, "intensity": cfg.intensity},
                           did_research=False, used_override=True, saved_override=False)

    did_research = False
    saved_override = False

    # **NEW RULE**: for entertainment definitional questions, prefer wikipedia immediately
    if AUTO_WIKI_FALLBACK and is_def_q and kind == "entertainment":
        ws = _wiki_summary(normalized)
        if ws and ws.get("extract"):
            cleaned = (
                f"{ws['title']}\n\n"
                f"Definition:\n- {ws['extract']}\n\n"
                f"Sources:\n- Wikipedia: {ws.get('url','')}\n"
            ).strip()

            saved_override = _save_auto_override(normalized, cleaned, sources=[ws.get("url", "Wikipedia")])
            cfg = load_theme()
            themed = apply_theme(cleaned, topic=normalized, cfg=cfg)
            return AskResponse(ok=True, topic=key, answer=themed, duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=True, used_override=False, saved_override=saved_override)

    # Normal brain ask
    raw_res = await _run_brain(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))

    # If weak, try /weblearn attempts, then wiki fallback
    if AUTO_WEBLEARN and _looks_low_quality(cleaned, kind, is_def_q):
        did_research = True

        for _ in range(1, max(1, AUTO_WEBLEARN_MAX_ATTEMPTS) + 1):
            raw_learn = await _run_brain(f"/weblearn {normalized}", timeout_s=AUTO_WEBLEARN_TIMEOUT_S)
            learned = _clean_repl_stdout(raw_learn.get("stdout", ""))
            if learned.strip():
                cleaned = learned
                raw_res = raw_learn
            if not _looks_low_quality(cleaned, kind, is_def_q):
                break

        # if still weak, wiki fallback
        if AUTO_WIKI_FALLBACK and _looks_low_quality(cleaned, kind, is_def_q):
            ws = _wiki_summary(normalized)
            if ws and ws.get("extract"):
                cleaned = (
                    f"{ws['title']}\n\n"
                    f"Definition:\n- {ws['extract']}\n\n"
                    f"Sources:\n- Wikipedia: {ws.get('url','')}\n"
                ).strip()
                saved_override = _save_auto_override(normalized, cleaned, sources=[ws.get("url", "Wikipedia")])

        # save good answers (but don't save low-trust domains)
        if not saved_override and not _looks_low_quality(cleaned, kind, is_def_q):
            doms = _extract_domains_from_text(cleaned)
            if not any(_domain_is_lowtrust(d) for d in doms):
                saved_override = _save_auto_override(normalized, cleaned, sources=doms)

        if _looks_low_quality(cleaned, kind, is_def_q):
            cleaned = (
                f"{normalized}\n\n"
                "I tried researching that, but I kept hitting low-quality/blocked pages.\n"
                "Try again in a minute or rephrase slightly.\n"
            ).strip()

    cfg = load_theme()
    themed = apply_theme(cleaned, topic=normalized, cfg=cfg)

    return AskResponse(
        ok=True,
        topic=key,
        answer=themed,
        duration_s=float(raw_res.get("duration_s", time.time() - t0)),
        raw=(raw_res if req.raw else None),
        theme={"theme": cfg.theme, "intensity": cfg.intensity},
        did_research=did_research,
        used_override=False,
        saved_override=saved_override,
    )
