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
VERSION = "0.5.0"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

PYTHON_BIN = os.environ.get("MS_PYTHON", "/usr/bin/python3")
MS_API_KEY = os.environ.get("MS_API_KEY", "")

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))

AUTO_WEBLEARN = os.environ.get("MS_AUTO_WEBLEARN", "1").strip().lower() in ("1", "true", "yes", "on")
AUTO_WEBLEARN_TIMEOUT_S = int(os.environ.get("MS_AUTO_WEBLEARN_TIMEOUT_S", "90"))
AUTO_WEBLEARN_MAX_ATTEMPTS = int(os.environ.get("MS_AUTO_WEBLEARN_MAX_ATTEMPTS", "4"))

CFG_DIR = Path(os.path.expanduser("~/.config/machinespirit"))
OVERRIDES_PATH = Path(os.environ.get("MS_OVERRIDES_PATH", str(CFG_DIR / "overrides.json"))).expanduser()

DEFAULT_AVOID_DOMAINS = [
    "fandom.com",
    "wikia.com",
    "steamcommunity.com",
    "reddit.com",
    "quora.com",
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
    domains = []
    for m in re.finditer(r"https?://([A-Za-z0-9\.\-]+)", text):
        d = (m.group(1) or "").lower().strip().lstrip(".")
        if d and d not in domains:
            domains.append(d)
    return domains


def _domain_is_fandom(d: str) -> bool:
    d = (d or "").lower()
    return ("fandom.com" in d) or ("wikia.com" in d)


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


def _looks_low_quality(answer: str, kind: str) -> bool:
    a = (answer or "").strip()
    al = a.lower()

    if not a:
        return True

    min_len = 90 if kind == "entertainment" else 140
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

    doms = _extract_domains_from_text(a)
    if any(_domain_is_fandom(d) for d in doms):
        return True

    return False


def _http_get_json(url: str, timeout_s: int = 12) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "MachineSpirit/0.5.0 (+local)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8", errors="replace"))


def _wiki_opensearch_url(query: str) -> Optional[str]:
    q = (query or "").strip()
    if not q:
        return None
    api = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": q,
        "limit": "1",
        "namespace": "0",
        "format": "json",
    }
    url = api + "?" + urllib.parse.urlencode(params)
    try:
        data = _http_get_json(url, timeout_s=12)
        if isinstance(data, list) and len(data) >= 4 and isinstance(data[3], list) and data[3]:
            u = (data[3][0] or "").strip()
            if u.startswith("http"):
                return u
    except Exception:
        return None
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


def _parse_correction(text: str) -> Optional[Tuple[str, str]]:
    """
    Accepts:
      No, it's actually: <TOPIC>
      <ANSWER...>

    or:
      no it is: <TOPIC>
      <ANSWER...>
    """
    if not text:
        return None
    raw = text.replace("’", "'")
    lines = raw.splitlines()
    if not lines:
        return None

    first = (lines[0] or "").strip()

    # allow "No, it's actually: Topic"
    m = re.match(r"^\s*no\s*,?\s*(it'?s|it\s+is)\s*(actually)?\s*:\s*(.+?)\s*$", first, flags=re.I)
    if not m:
        return None

    topic = (m.group(3) or "").strip()
    answer = "\n".join(lines[1:]).strip()

    if not topic:
        return None
    if not answer:
        # If they put everything on one line, treat the rest of that line as "topic only" and refuse.
        return ("__missing__", "")

    return (topic, answer)


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
        "auto_weblearn": {"enabled": AUTO_WEBLEARN, "max_attempts": AUTO_WEBLEARN_MAX_ATTEMPTS},
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

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")

    # Theme commands
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
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False, used_override=False)

        if len(parts) >= 2 and parts[1].lower() in ("off", "none", "disable", "disabled"):
            cfg = save_theme("none", "light")
            return AskResponse(ok=True, topic="theme", answer="Theme disabled.", duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False, used_override=False)

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
            return AskResponse(ok=True, topic="theme",
                               answer=f"Theme set to: {cfg.theme} ({cfg.intensity}).",
                               duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False, used_override=False)

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [light|heavy]")

    # Conversational correction (no commands)
    corr = _parse_correction(text)
    if corr:
        topic, answer = corr
        if topic == "__missing__" or not answer:
            cfg = load_theme()
            msg = (
                "Correction format needs 2 parts:\n\n"
                "No, it's actually: <TOPIC>\n"
                "<your corrected answer...>\n"
            ).strip()
            themed = apply_theme(msg, topic="correction", cfg=cfg)
            return AskResponse(ok=True, topic="correction", answer=themed, duration_s=time.time() - t0,
                               theme={"theme": cfg.theme, "intensity": cfg.intensity},
                               did_research=False, used_override=False)

        key = _topic_key(topic)
        overrides = _load_overrides()
        overrides[key] = {
            "topic": _normalize_topic(topic),
            "answer": answer.strip(),
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": "user_override",
            "confidence": 0.92,
        }
        _save_overrides(overrides)

        cfg = load_theme()
        ack = f"Saved correction for: {overrides[key]['topic']}\n\nAsk it again anytime and I’ll use your version."
        themed = apply_theme(ack, topic=overrides[key]["topic"], cfg=cfg)

        return AskResponse(ok=True, topic=key, answer=themed, duration_s=time.time() - t0,
                           theme={"theme": cfg.theme, "intensity": cfg.intensity},
                           did_research=False, used_override=True)

    normalized = _normalize_topic(text)
    key = _topic_key(normalized)
    kind = _topic_kind(normalized)

    # Override wins immediately (no research)
    overrides = _load_overrides()
    if key in overrides and isinstance(overrides[key], dict) and overrides[key].get("answer"):
        cfg = load_theme()
        ans = overrides[key]["answer"]
        themed = apply_theme(ans, topic=normalized, cfg=cfg)
        return AskResponse(ok=True, topic=key, answer=themed, duration_s=time.time() - t0,
                           theme={"theme": cfg.theme, "intensity": cfg.intensity},
                           did_research=False, used_override=True)

    # Normal brain call
    raw_res = await _run_brain(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))

    did_research = False

    if AUTO_WEBLEARN and _looks_low_quality(cleaned, kind):
        did_research = True

        avoid: List[str] = list(DEFAULT_AVOID_DOMAINS)
        for d in _extract_domains_from_text(cleaned):
            if d and d not in avoid:
                avoid.append(d)

        last_answer = cleaned
        last_raw = raw_res

        for attempt in range(1, max(1, AUTO_WEBLEARN_MAX_ATTEMPTS) + 1):
            if kind == "entertainment" and attempt >= 2:
                wiki_url = _wiki_opensearch_url(normalized)
                if wiki_url:
                    cmd = f"/weburl {wiki_url}"
                else:
                    cmd = f"/weblearn {normalized} site:wikipedia.org"
            else:
                cmd = f"/weblearn {normalized}"

            raw_learn = await _run_brain(cmd, timeout_s=AUTO_WEBLEARN_TIMEOUT_S)
            learned = _clean_repl_stdout(raw_learn.get("stdout", ""))

            if learned.strip():
                last_answer = learned
                last_raw = raw_learn

            if _looks_low_quality(learned, kind):
                for d in _extract_domains_from_text(learned):
                    if d and d not in avoid:
                        avoid.append(d)
                continue

            cleaned = learned
            raw_res = raw_learn
            break

        if _looks_like_block_or_captcha(last_answer):
            cleaned = (
                f"{normalized}\n\n"
                "I hit a blocked/captcha page while researching that, so I couldn’t pull a clean explanation right now.\n"
                "Ask again and I’ll search different sources.\n"
            ).strip()
            raw_res = last_raw
        elif _looks_like_nav_legal_junk(last_answer):
            cleaned = (
                f"{normalized}\n\n"
                "I found a page that was mostly navigation/legal text, not a real explanation. I retried, but didn’t get a clean source yet.\n"
                "Ask again and I’ll keep searching.\n"
            ).strip()
            raw_res = last_raw
        elif _looks_low_quality(last_answer, kind):
            cleaned = (
                f"{normalized}\n\n"
                "I tried researching that, but the sources I found were too low-quality to save as an answer.\n"
                "Try asking again with a little more detail.\n"
            ).strip()
            raw_res = last_raw

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
    )
