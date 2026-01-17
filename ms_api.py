#!/usr/bin/env python3
import os
import sys
import re
import time
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

import fcntl
from fastapi import FastAPI, HTTPException, Request, Query
from pydantic import BaseModel

APP_NAME = "MachineSpirit API"
VERSION = "0.3.3"

BASE_DIR = Path(__file__).resolve().parent
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(BASE_DIR / "brain.py"))).resolve()
REPO_DIR = BASE_DIR

PYTHON_BIN = os.environ.get("MS_PYTHON_BIN", "/usr/bin/python3")

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock"))).resolve()
LOCK_WAIT_S = float(os.environ.get("MS_LOCK_WAIT_S", "0"))

MS_API_KEY = (os.environ.get("MS_API_KEY") or "").strip()

app = FastAPI(title=APP_NAME, version=VERSION)


# ----------------------------
# Models
# ----------------------------
class AskRequest(BaseModel):
    text: str
    timeout_s: Optional[float] = 20.0


class AskResponse(BaseModel):
    ok: bool
    topic: Optional[str] = None
    answer: str
    duration_s: float = 0.0


class RunResult(BaseModel):
    ok: bool
    exit_code: int
    args: List[str]
    duration_s: float
    stdout: str = ""
    stderr: str = ""
    answer: Optional[str] = None


# ----------------------------
# Auth
# ----------------------------
def _require_auth(request: Request) -> None:
    if not MS_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: MS_API_KEY is not set")

    got = request.headers.get("x-api-key", "")
    if not got or got != MS_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized (missing/invalid x-api-key)")


# ----------------------------
# Lock
# ----------------------------
class _FileLock:
    def __init__(self, path: Path, wait_s: float):
        self.path = path
        self.wait_s = wait_s
        self.f = None

    async def __aenter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+")
        start = time.time()

        while True:
            try:
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if self.wait_s <= 0:
                    raise HTTPException(status_code=409, detail="Brain is busy (lock held). Try again.")
                if (time.time() - start) > self.wait_s:
                    raise HTTPException(status_code=409, detail="Brain is busy (lock timeout). Try again.")
                await asyncio.sleep(0.05)

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self.f:
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                if self.f:
                    self.f.close()
            except Exception:
                pass


# ----------------------------
# Brain runners
# ----------------------------
def _brain_repl_args() -> List[str]:
    # REPL mode (stdin-driven)
    return [PYTHON_BIN, str(BRAIN_PATH)]


def _brain_headless_webqueue_args(limit: int) -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH), "--webqueue", "--limit", str(limit), "--confirm"]


def _brain_headless_curiosity_args(n: int) -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH), "--curiosity", "--n", str(n)]


async def _run_process(args: List[str], stdin_text: Optional[str] = None, timeout_s: Optional[float] = None) -> RunResult:
    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate((stdin_text or "").encode("utf-8") if stdin_text is not None else None),
                timeout=timeout_s if timeout_s else None,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            raise HTTPException(status_code=408, detail="Brain timed out")

        out = (out_b or b"").decode("utf-8", errors="replace")
        err = (err_b or b"").decode("utf-8", errors="replace")
        code = int(proc.returncode or 0)

        return RunResult(
            ok=(code == 0),
            exit_code=code,
            args=args,
            duration_s=(time.time() - t0),
            stdout=out,
            stderr=err,
        )
    except HTTPException:
        raise
    except Exception as e:
        return RunResult(
            ok=False,
            exit_code=1,
            args=args,
            duration_s=(time.time() - t0),
            stdout="",
            stderr=str(e),
        )


# ----------------------------
# Output cleaning + “normal question” handling
# ----------------------------
_PROMPT_RE = re.compile(r'^\s*>\s*(.*)$')


def _normalize_user_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""

    # remove trailing question marks etc (keep it simple)
    t = t.strip().rstrip("?").strip()

    low = t.lower().strip()

    # common natural language wrappers
    prefixes = [
        "what is ",
        "what's ",
        "whats ",
        "define ",
        "definition of ",
        "explain ",
        "tell me about ",
    ]
    for p in prefixes:
        if low.startswith(p):
            return t[len(p):].strip()

    return t


def _clean_repl_stdout(raw: str) -> str:
    """
    Turn REPL-style stdout into a clean answer.

    Handles BOTH prompt styles:
      - '>CIDR'
      - '> CIDR'
    And removes any shutdown prompt like:
      - '> Shutting down.'
    """
    if not raw:
        return ""

    lines = raw.splitlines()
    topic = ""
    body: List[str] = []
    seen_topic_prompt = False

    for line in lines:
        s = line.strip()

        # Drop banner
        if s.startswith("Machine Spirit brain online."):
            continue

        # Prompt lines
        pm = _PROMPT_RE.match(line)
        if pm:
            prompt_text = (pm.group(1) or "").strip()

            # stop on shutdown prompt
            if "shutting down" in prompt_text.lower():
                break

            # first prompt is the topic
            if not seen_topic_prompt and prompt_text:
                topic = prompt_text
                seen_topic_prompt = True
                continue

            # any later prompt means end of answer
            break

        # drop stray shutdown lines anywhere
        if "shutting down" in s.lower():
            continue

        body.append(line.rstrip())

    # trim leading blanks
    while body and body[0].strip() == "":
        body.pop(0)

    body_text = "\n".join(body).strip()

    if topic and body_text:
        return f"{topic}\n\n{body_text}"
    if topic:
        return topic
    return body_text


def _parse_alias_suggestion(clean_answer: str) -> Optional[str]:
    """
    Detect:  Suggestion: /alias what is nat|nat
    Return:  nat
    """
    s = (clean_answer or "").strip()
    m = re.search(r'^Suggestion:\s*/alias\s+.+?\|(.+?)\s*$', s, flags=re.IGNORECASE)
    if not m:
        return None
    return (m.group(1) or "").strip()


def _looks_like_disambiguation(clean_answer: str) -> bool:
    low = (clean_answer or "").lower()
    return ("may refer to" in low) or (low.strip().endswith("may refer to:"))


_FALLBACKS: Dict[str, str] = {
    "nat": (
        "NAT (Network Address Translation)\n\n"
        "Definition:\n"
        "- A router feature that translates private IP addresses (like 192.168.x.x) into a public IP address so many devices can share one internet connection.\n\n"
        "Key points:\n"
        "- Often used in home networks.\n"
        "- Helps conserve public IPv4 addresses.\n"
        "- Not a firewall by itself, but it can reduce unsolicited inbound connections.\n\n"
        "Sources:\n"
        "- RFC 3022 (Traditional NAT): https://www.rfc-editor.org/rfc/rfc3022"
    )
}


async def _ask_topic(topic: str, timeout_s: float) -> RunResult:
    # feed one line to REPL
    stdin_text = topic.strip() + "\n"
    return await _run_process(_brain_repl_args(), stdin_text=stdin_text, timeout_s=timeout_s)


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
async def root() -> Dict[str, Any]:
    return {"ok": True, "app": APP_NAME, "version": VERSION}


@app.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    return {
        "ok": True,
        "app": APP_NAME,
        "version": VERSION,
        "brain_path": str(BRAIN_PATH),
        "repo_dir": str(REPO_DIR),
        "python": PYTHON_BIN,
        "lock_path": str(LOCK_PATH),
        "lock_wait_s": LOCK_WAIT_S,
    }


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    """
    Production-friendly endpoint:
    Returns ONLY a clean answer (no stdout/stderr spam).
    Also auto-handles natural language like "what is nat".
    Also auto-follows alias suggestions from the brain.
    """
    _require_auth(request)

    raw_text = (req.text or "").strip()
    topic = _normalize_user_text(raw_text)
    if not topic:
        raise HTTPException(status_code=422, detail="text is required")

    timeout_s = float(req.timeout_s or 20.0)

    async with _FileLock(LOCK_PATH, LOCK_WAIT_S):
        res = await _ask_topic(topic, timeout_s)
        clean = _clean_repl_stdout(res.stdout)

        # If brain returns alias suggestion, auto-follow it
        sug = _parse_alias_suggestion(clean)
        if sug and sug.lower() != topic.lower():
            topic = sug
            res = await _ask_topic(topic, timeout_s)
            clean = _clean_repl_stdout(res.stdout)

        # If still weak/disambiguation for certain acronyms, try fallback
        if topic.lower() in _FALLBACKS and _looks_like_disambiguation(clean):
            clean = _FALLBACKS[topic.lower()]

        ok = bool(clean.strip()) and res.exit_code == 0

        return AskResponse(
            ok=ok,
            topic=topic,
            answer=clean.strip(),
            duration_s=res.duration_s,
        )


@app.post("/ask_debug", response_model=RunResult)
async def ask_debug(req: AskRequest, request: Request) -> RunResult:
    """
    Debug endpoint:
    Returns full stdout/stderr + answer.
    """
    _require_auth(request)

    raw_text = (req.text or "").strip()
    topic = _normalize_user_text(raw_text)
    if not topic:
        raise HTTPException(status_code=422, detail="text is required")

    timeout_s = float(req.timeout_s or 20.0)

    async with _FileLock(LOCK_PATH, LOCK_WAIT_S):
        res = await _ask_topic(topic, timeout_s)
        clean = _clean_repl_stdout(res.stdout)

        sug = _parse_alias_suggestion(clean)
        if sug and sug.lower() != topic.lower():
            topic = sug
            res = await _ask_topic(topic, timeout_s)
            clean = _clean_repl_stdout(res.stdout)

        if topic.lower() in _FALLBACKS and _looks_like_disambiguation(clean):
            clean = _FALLBACKS[topic.lower()]

        res.answer = clean.strip()
        return res


@app.get("/queuehealth", response_model=RunResult)
async def queuehealth(request: Request, timeout_s: float = Query(20.0)) -> RunResult:
    _require_auth(request)
    async with _FileLock(LOCK_PATH, LOCK_WAIT_S):
        return await _run_process(_brain_repl_args(), stdin_text="/queuehealth\n", timeout_s=timeout_s)


@app.get("/needsources", response_model=RunResult)
async def needsources(request: Request, limit: int = Query(20), timeout_s: float = Query(20.0)) -> RunResult:
    _require_auth(request)
    cmd = f"/needsources {int(limit)}"
    async with _FileLock(LOCK_PATH, LOCK_WAIT_S):
        return await _run_process(_brain_repl_args(), stdin_text=cmd + "\n", timeout_s=timeout_s)


@app.post("/run/webqueue", response_model=RunResult)
async def run_webqueue(request: Request, limit: int = Query(3), confirm: bool = Query(False), timeout_s: float = Query(120.0)) -> RunResult:
    _require_auth(request)
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required")
    async with _FileLock(LOCK_PATH, LOCK_WAIT_S):
        return await _run_process(_brain_headless_webqueue_args(int(limit)), timeout_s=timeout_s)


@app.post("/run/curiosity", response_model=RunResult)
async def run_curiosity(request: Request, n: int = Query(3), confirm: bool = Query(False), timeout_s: float = Query(120.0)) -> RunResult:
    _require_auth(request)
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required")
    async with _FileLock(LOCK_PATH, LOCK_WAIT_S):
        return await _run_process(_brain_headless_curiosity_args(int(n)), timeout_s=timeout_s)


# Swagger/OpenAPI: mark everything except a few as requiring x-api-key
def custom_openapi():
    from fastapi.openapi.utils import get_openapi

    if getattr(app, "openapi_schema", None):
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )

    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "x-api-key",
    }

    public_paths = {"/", "/openapi.json", "/docs", "/redoc"}
    for path, methods in schema.get("paths", {}).items():
        if path in public_paths:
            continue
        for method, op in methods.items():
            if method.lower() in {"get", "post", "put", "delete", "patch", "options", "head"}:
                op.setdefault("security", [])
                req = {"ApiKeyAuth": []}
                if req not in op["security"]:
                    op["security"].append(req)

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi
