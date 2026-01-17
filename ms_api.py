#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

import fcntl
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

APP_NAME = "MachineSpirit API"
API_VERSION = "0.3.1"

REPO_DIR = Path(__file__).resolve().parent
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

# Important: set MS_PYTHON=/usr/bin/python3 in ~/.config/machinespirit/api.env
PYTHON_BIN = os.environ.get("MS_PYTHON", sys.executable)

DEFAULT_TIMEOUT_S = int(os.environ.get("MS_TIMEOUT_S", "60"))
MS_API_KEY = os.environ.get("MS_API_KEY", "").strip()

LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock"))).resolve()
LOCK_WAIT_S = float(os.environ.get("MS_LOCK_WAIT_S", "0") or "0")

RUN_LOCK = asyncio.Lock()

app = FastAPI(title=APP_NAME, version=API_VERSION)


class RunResult(BaseModel):
    ok: bool
    exit_code: int
    args: List[str]
    duration_s: float
    stdout: str
    stderr: str
    answer: Optional[str] = None


class AskRequest(BaseModel):
    text: str = Field(..., description="User topic/question (single line).")
    timeout_s: Optional[int] = Field(None, description="Override timeout seconds.")


class CommandRequest(BaseModel):
    line: str = Field(..., description="A single command line as typed into brain.py interactive.")
    timeout_s: Optional[int] = Field(None, description="Override timeout seconds.")


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _require_auth(request: Request) -> None:
    ip = _client_ip(request)
    if MS_API_KEY:
        supplied = request.headers.get("x-api-key", "")
        if supplied != MS_API_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized (missing/invalid x-api-key).")
    else:
        if ip not in ("127.0.0.1", "::1"):
            raise HTTPException(status_code=403, detail="Forbidden: localhost-only unless MS_API_KEY is set.")


def _require_confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="This endpoint mutates state. Re-run with confirm=true")


def _strip_ansi(s: str) -> str:
    # Basic ANSI escape strip
    import re
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s or "")


def _letters_only(s: str) -> str:
    import re
    return re.sub(r"[^a-z]+", "", (s or "").lower())


def _clean_repl_stdout(raw: str) -> str:
    """
    Remove REPL banner/prompt/shutdown from brain stdout.
    """
    if not raw:
        return ""

    import re
    out: List[str] = []
    prompt_topic: Optional[str] = None

    for line in raw.splitlines():
        line2 = _strip_ansi(line)
        # replace control chars with spaces
        line2 = "".join((ch if ch >= " " else " ") for ch in line2)
        s = line2.strip()
        if not s:
            out.append("")
            continue

        norm = _letters_only(s)

        # banner
        if norm.startswith("machinespiritbrainonline"):
            continue
        if ("typeamessage" in norm) and ("ctrlc" in norm):
            continue

        # prompt "> TOPIC" (allow leading spaces)
        m = re.match(r"^\s*>\s*(.+?)\s*$", line2)
        if m:
            maybe = m.group(1).strip()
            if maybe:
                prompt_topic = maybe
            continue

        # shutdown (this catches normal + weird versions)
        if "shuttingdown" in norm:
            continue

        out.append(line2.rstrip())

    # drop leading blanks
    while out and out[0].strip() == "":
        out.pop(0)

    # Add prompt topic on top if it looks like a simple topic
    if prompt_topic:
        if not out or out[0].strip() != prompt_topic:
            out = [prompt_topic, ""] + out

    cleaned = "\n".join(out).strip()
    return cleaned + ("\n" if cleaned else "")


def _finalize_answer(cleaned: str, requested_text: str) -> str:
    """
    Guaranteed final cleanup:
      - removes ANY shutdown line even if it slipped through
      - ensures topic header for simple one-word topics (like 'cidr')
    """
    lines = (cleaned or "").splitlines()
    out: List[str] = []

    for ln in lines:
        if "shuttingdown" in _letters_only(ln):
            continue
        out.append(ln.rstrip())

    while out and out[0].strip() == "":
        out.pop(0)

    req = (requested_text or "").strip()

    # Only enforce a header when request looks like a "topic", not a full question.
    # (prevents weird headers for long questions)
    if req and ("\n" not in req):
        words = [w for w in req.split() if w.strip()]
        looks_like_topic = (len(words) <= 3) and (len(req) <= 40) and (not req.endswith("?"))
        if looks_like_topic:
            first = next((x for x in out if x.strip()), "")
            if _letters_only(first) != _letters_only(req):
                out = [req.upper(), ""] + out

    final = "\n".join(out).strip()
    return final + ("\n" if final else "")


async def _acquire_lock(wait_s: float):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "a+")
    deadline = time.time() + max(0.0, wait_s)

    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if wait_s <= 0:
                fh.close()
                return None
            if time.time() >= deadline:
                fh.close()
                return None
            await asyncio.sleep(0.2)


def _release_lock(fh) -> None:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


async def _run_process(args: List[str], stdin_text: Optional[str] = None, timeout_s: Optional[int] = None) -> RunResult:
    if not BRAIN_PATH.exists():
        raise HTTPException(status_code=500, detail=f"brain.py not found at {BRAIN_PATH}")

    timeout = int(timeout_s or DEFAULT_TIMEOUT_S)
    t0 = time.time()

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    async with RUN_LOCK:
        lock_fh = await _acquire_lock(LOCK_WAIT_S)
        if lock_fh is None:
            raise HTTPException(status_code=409, detail=f"Busy: brain lock held ({LOCK_PATH}). Try again.")

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(REPO_DIR),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            in_bytes = (stdin_text or "").encode("utf-8", errors="replace")

            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(in_bytes), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.wait()
                dt = time.time() - t0
                return RunResult(ok=False, exit_code=124, args=args, duration_s=dt, stdout="", stderr=f"Timeout after {timeout}s")
        finally:
            _release_lock(lock_fh)

    dt = time.time() - t0
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    code = int(proc.returncode or 0)
    return RunResult(ok=(code == 0), exit_code=code, args=args, duration_s=dt, stdout=stdout, stderr=stderr)


def _brain_repl_args() -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH)]


def _brain_headless_webqueue_args(limit: int) -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH), "--webqueue", "--limit", str(int(limit))]


def _brain_headless_curiosity_args(n: int) -> List[str]:
    return [PYTHON_BIN, str(BRAIN_PATH), "--curiosity", "--n", str(int(n))]


@app.get("/")
async def root() -> Dict[str, Any]:
    return {"ok": True, "app": APP_NAME, "version": API_VERSION, "docs": "/docs", "hint": "Most endpoints require x-api-key. Try /health."}


@app.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    return {
        "ok": True,
        "app": APP_NAME,
        "version": API_VERSION,
        "brain_path": str(BRAIN_PATH),
        "repo_dir": str(REPO_DIR),
        "python": PYTHON_BIN,
        "localhost_only": (not bool(MS_API_KEY)),
        "lock_path": str(LOCK_PATH),
        "lock_wait_s": LOCK_WAIT_S,
    }


@app.post("/ask", response_model=RunResult)
async def ask(req: AskRequest, request: Request) -> RunResult:
    _require_auth(request)

    # Run the brain in REPL mode and feed the topic as a single line.
    topic = (req.text or "").strip()
    if not topic:
        raise HTTPException(status_code=422, detail="text is required")

    stdin_text = topic + "\n"
    res = await _run_process(_brain_repl_args(), stdin_text=stdin_text, timeout_s=req.timeout_s)

    # If you have cleaner logic, keep using it; otherwise answer=stdout is fine.
    try:
        res.answer = _clean_repl_stdout(res.stdout)
    except Exception:
        res.answer = res.stdout or ""

    return res
@app.post("/teach", response_model=RunResult)
async def teach(req: CommandRequest, request: Request, confirm: bool = Query(False)) -> RunResult:
    _require_auth(request)
    _require_confirm(confirm)
    line = req.line.strip()
    if not line:
        raise HTTPException(status_code=400, detail="Empty line.")
    return await _run_process(_brain_repl_args(), stdin_text=line + "\n", timeout_s=req.timeout_s)


@app.get("/queuehealth", response_model=RunResult)
async def queuehealth(request: Request, timeout_s: Optional[int] = None) -> RunResult:
    _require_auth(request)
    return await _run_process(_brain_repl_args(), stdin_text="/queuehealth\n", timeout_s=timeout_s)


@app.get("/needsources", response_model=RunResult)
async def needsources(
    request: Request,
    limit: Optional[int] = Query(None, ge=1, le=500),
    timeout_s: Optional[int] = None,
) -> RunResult:
    _require_auth(request)
    cmd = "/needsources" + (f" {int(limit)}" if limit else "")
    return await _run_process(_brain_repl_args(), stdin_text=cmd + "\n", timeout_s=timeout_s)


@app.post("/run/webqueue", response_model=RunResult)
async def run_webqueue(
    request: Request,
    limit: int = Query(5, ge=1, le=100),
    confirm: bool = Query(False),
    timeout_s: Optional[int] = None,
) -> RunResult:
    _require_auth(request)
    _require_confirm(confirm)
    return await _run_process(_brain_headless_webqueue_args(limit), timeout_s=timeout_s)


@app.post("/run/curiosity", response_model=RunResult)
async def run_curiosity(
    request: Request,
    n: int = Query(10, ge=1, le=500),
    confirm: bool = Query(False),
    timeout_s: Optional[int] = None,
) -> RunResult:
    _require_auth(request)
    _require_confirm(confirm)
    return await _run_process(_brain_headless_curiosity_args(n), timeout_s=timeout_s)


@app.post("/run/selftest", response_model=RunResult)
async def run_selftest(request: Request, timeout_s: Optional[int] = None) -> RunResult:
    _require_auth(request)
    args = [PYTHON_BIN, str(BRAIN_PATH), "--selftest"]
    return await _run_process(args, timeout_s=timeout_s)

def custom_openapi():
    """
    Swagger/OpenAPI:
      - adds an Authorize button for x-api-key
      - marks protected endpoints as requiring ApiKeyAuth
    """
    if getattr(app, "openapi_schema", None):
        return app.openapi_schema

    schema = get_openapi(
        title=getattr(app, "title", "MachineSpirit API"),
        version=str(getattr(app, "version", "0.0.0")),
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
            if method.lower() in {"get","post","put","delete","patch","options","head"}:
                op.setdefault("security", [])
                req = {"ApiKeyAuth": []}
                if req not in op["security"]:
                    op["security"].append(req)

    app.openapi_schema = schema
    return app.openapi_schema

app.openapi = custom_openapi
