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

app = FastAPI(title=APP_NAME, version=VERSION)

# Prevent overlapping brain subprocess calls
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
    theme: Optional[Dict[str, str]] = None


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
# Brain helpers
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


def _looks_low_quality(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return True
    if "may refer to" in a:
        return True
    if re.search(r"(?is)sources:\s*-\s*$", answer.strip()):
        return True
    # common junk blocks we’ve seen
    junk = [
        "agree & join linkedin",
        "cookie policy",
        "sign in to view more content",
        "create your free account",
        "by clicking continue",
    ]
    if any(j in a for j in junk):
        return True
    return False


def _strip_known_web_junk(text: str) -> str:
    if not text:
        return ""
    drop_contains = [
        "Agree & Join LinkedIn",
        "By clicking Continue",
        "Cookie Policy",
        "Sign in to view more content",
        "Create your free account",
        "Welcome back",
        "Forgot password",
    ]
    out: List[str] = []
    for ln in text.splitlines():
        if any(bad.lower() in ln.lower() for bad in drop_contains):
            continue
        out.append(ln.rstrip())

    # collapse multiple blanks
    cleaned: List[str] = []
    blank = 0
    for ln in out:
        if ln.strip() == "":
            blank += 1
            if blank <= 1:
                cleaned.append("")
        else:
            blank = 0
            cleaned.append(ln)
    return "\n".join(cleaned).strip()


def _already_structured(text: str) -> bool:
    t = text or ""
    return bool(re.search(r"(?im)^\s*definition\s*:\s*$", t)) and bool(re.search(r"(?im)^\s*key points\s*:\s*$", t))


def _split_sources(text: str) -> Tuple[str, List[str]]:
    if not text:
        return "", []
    m = re.search(r"(?is)\n\s*sources\s*:\s*\n", text)
    if not m:
        return text.strip(), []
    main = text[: m.start()].rstrip()
    tail = text[m.end():].strip()

    src: List[str] = []
    for ln in tail.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("-"):
            s = s[1:].strip()
        src.append(s)

    seen = set()
    uniq: List[str] = []
    for s in src:
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
    return main.strip(), uniq


def _bullets_from_text(text: str, max_items: int = 6) -> List[str]:
    if not text:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    existing = [ln.lstrip("-• ").strip() for ln in lines if ln.lstrip().startswith(("-", "•"))]
    existing = [x for x in existing if x]
    if existing:
        return existing[:max_items]

    blob = " ".join(lines)
    parts = re.split(r"(?<=[.!?])\s+", blob)
    parts = [p.strip() for p in parts if p.strip()]
    return parts[:max_items]


def _synthesize_answer(cleaned: str, topic: str) -> str:
    text = _strip_known_web_junk(cleaned)

    if _already_structured(text):
        return text.strip()

    if len(text.strip()) < 140:
        return text.strip()

    main, sources = _split_sources(text)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", main) if p.strip()]
    first = paragraphs[0] if paragraphs else main.strip()

    sent = re.split(r"(?<=[.!?])\s+", first)
    sent = [s.strip() for s in sent if s.strip()]
    definition = " ".join(sent[:2]).strip()

    rest = "\n\n".join(paragraphs[1:]).strip() if len(paragraphs) > 1 else ""
    bullets = _bullets_from_text(rest, max_items=6)

    if not bullets:
        remainder = main.replace(first, "", 1).strip()
        bullets = _bullets_from_text(remainder, max_items=6)

    out: List[str] = []
    if topic:
        out.append(topic.strip())
        out.append("")

    out.append("Definition:")
    out.append(f"- {definition}" if definition else "- (no clean definition yet)")
    out.append("")
    out.append("Key points:")
    if bullets:
        for b in bullets[:6]:
            out.append(f"- {b}")
    else:
        out.append("- (no clean key points yet)")

    if sources:
        out.append("")
        out.append("Sources:")
        for s in sources[:6]:
            out.append(f"- {s}")

    return "\n".join(out).strip()


FALLBACK_MINI: Dict[str, str] = {
    "nat": (
        "NAT (Network Address Translation)\n\n"
        "Definition:\n"
        "- A router feature that translates private IP addresses (like 192.168.x.x) to a public IP address so multiple devices can share one internet connection.\n\n"
        "Key points:\n"
        "- Common in home networks.\n"
        "- Helps conserve IPv4 addresses.\n"
        "- Not a firewall by itself, but it reduces unsolicited inbound connections.\n\n"
        "Sources:\n"
        "- RFC 3022: https://www.rfc-editor.org/rfc/rfc3022\n"
    ),
}


async def _run_brain_repl(topic_line: str, timeout_s: int) -> Dict[str, Any]:
    LOCK_PATH.touch(exist_ok=True)

    async with _BRAIN_LOCK:
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
                proc.communicate(input=(topic_line + "\n").encode("utf-8")),
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
        "ui_hint": "Main UI is on port 8020 (MachineSpirit UI).",
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

    # Allow changing theme by typing /theme into chat/UI
    if text.lower().startswith("/theme"):
        parts = text.split()

        def _map_intensity(s: str) -> str:
            s = (s or "").strip().lower()
            if s in ("1", "light", "lite"):
                return "light"
            if s in ("2", "heavy", "hard"):
                return "heavy"
            return "light"

        # /theme or /theme status
        if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() == "status"):
            cfg = load_theme()
            choices = ui_intensity_choices()
            msg = (
                f"Theme is currently: {cfg.theme} ({cfg.intensity})\n\n"
                "Set it like:\n"
                "- /theme off\n"
                "- /theme set Warhammer 40k 1\n"
                "- /theme set Warhammer 40k 2\n"
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
                theme={"theme": cfg.theme, "intensity": cfg.intensity},
            )

        # /theme off
        if len(parts) >= 2 and parts[1].lower() in ("off", "none", "disable", "disabled"):
            cfg = save_theme("none", "light")
            return AskResponse(
                ok=True,
                topic="theme",
                answer="Theme disabled.",
                duration_s=time.time() - t0,
                theme={"theme": cfg.theme, "intensity": cfg.intensity},
            )

        # /theme set <name...> <light|heavy|1|2?>
        if len(parts) >= 3 and parts[1].lower() == "set":
            intensity = "light"
            theme_name = " ".join(parts[2:]).strip()

            if parts[-1].lower() in ("light", "heavy", "1", "2"):
                intensity = _map_intensity(parts[-1])
                theme_name = " ".join(parts[2:-1]).strip()

            if not theme_name:
                raise HTTPException(status_code=422, detail="Theme name is required (example: /theme set Warhammer 40k 1)")

            cfg = save_theme(theme_name, intensity)
            return AskResponse(
                ok=True,
                topic="theme",
                answer=f"Theme set to: {cfg.theme} ({cfg.intensity}).",
                duration_s=time.time() - t0,
                theme={"theme": cfg.theme, "intensity": cfg.intensity},
            )

        raise HTTPException(status_code=422, detail="Theme command format: /theme, /theme off, or /theme set <name> [1|2|light|heavy]")

    # normal ask
    normalized = _normalize_topic(text)
    raw_res = await _run_brain_repl(normalized, timeout_s=int(req.timeout_s or 25))
    cleaned = _clean_repl_stdout(raw_res.get("stdout", ""))

    topic_key = normalized.strip().lower()

    if _looks_low_quality(cleaned):
        if topic_key in FALLBACK_MINI:
            cleaned = FALLBACK_MINI[topic_key]
        else:
            cleaned = (
                f"{normalized}\n\n"
                "I don’t have a clean answer stored yet.\n\n"
                "Next:\n"
                "- Run learning (safe): /run/webqueue?limit=3&confirm=true\n"
                "- Or in brain.py: /weblearn <topic>\n"
            ).strip()
    else:
        cleaned = _synthesize_answer(cleaned, topic=normalized)

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
