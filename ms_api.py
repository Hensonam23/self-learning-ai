#!/usr/bin/env python3
"""
MachineSpirit API
- Non-invasive wrapper: runs brain.py as a subprocess
- Safe-by-default:
    * If MS_API_KEY is set: require header x-api-key
    * If not set: localhost only
- Confirm-before-action: mutating endpoints require confirm=true
- Cross-process lock: prevents API + timers from running brain.py at the same time
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

import fcntl
from fastapi import FastAPI, HTTPException, Request, Query
from pydantic import BaseModel, Field

APP_NAME = "MachineSpirit API"

REPO_DIR = Path(__file__).resolve().parent
BRAIN_PATH = Path(os.environ.get("MS_BRAIN_PATH", str(REPO_DIR / "brain.py"))).resolve()

# IMPORTANT:
# - uvicorn runs in the venv python
# - brain subprocess should use /usr/bin/python3 for consistency with timers
PYTHON_BIN = os.environ.get("MS_PYTHON", sys.executable)

DEFAULT_TIMEOUT_S = int(os.environ.get("MS_TIMEOUT_S", "60"))

# If set, require header x-api-key == MS_API_KEY. If not set, localhost only.
MS_API_KEY = os.environ.get("MS_API_KEY", "").strip()

# Cross-process lock file (shared with systemd services)
LOCK_PATH = Path(os.environ.get("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock"))).resolve()
LOCK_WAIT_S = float(os.environ.get("MS_LOCK_WAIT_S", "0") or "0")  # 0 = don't wait

# Serialize API requests (does not cover systemd timers, that's why we also use LOCK_PATH)
RUN_LOCK = asyncio.Lock()

app = FastAPI(title=APP_NAME, version="0.3.0")


class RunResult(BaseModel):
    ok: bool
    exit_code: int
    args: List[str]
    duration_s: float
    stdout: str
    stderr: str
    answer: Optional[str] = None  # Cleaned answer for /ask


class AskRequest(BaseModel):
    text: str = Field(..., description="User question / prompt to feed to brain REPL (single line).")
    timeout_s: Optional[int] = Field(None, description="Override default timeout seconds for this request.")


class CommandRequest(BaseModel):
    line: str = Field(..., description="A single command line exactly as you'd type in brain.py interactive mode.")
    timeout_s: Optional[int] = Field(None, description="Override default timeout seconds for this request.")


def _client_ip(request: Request) -> str:
    if request.client is None:
        return ""
    return request.client.host or ""


def _require_auth(request: Request) -> None:
    ip = _client_ip(request)
    if MS_API_KEY:
        supplied = request.headers.get("x-api-key", "")
        if supplied != MS_API_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized (missing/invalid x-api-key).")
    else:
        if ip not in ("127.0.0.1", "::1"):
            raise HTTPException(
                status_code=403,
                detail="Forbidden: API is localhost-only unless MS_API_KEY is set.",
            )


def _require_confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="This endpoint mutates state. Re-run with confirm=true")


def _clean_repl_stdout(raw: str) -> str:
    """
    Clean REPL output so UI can display it without the banner/prompt.
    Keeps the topic (from the "> TOPIC" line) as the first line when present.
    """
    if not raw:
        return ""

    lines = raw.splitlines()
    out: List[str] = []
    prompt_topic: Optional[str] = None

    for line in lines:
        s = line.strip()

        # Banner lines
        if s.startswith("Machine Spirit brain online."):
            continue
        if "Type a message" in s and "Ctrl+C" in s:
            continue

        # Prompt line: "> CIDR"
        if s.startswith(">"):
            maybe = s.lstrip(">").strip()
            if maybe:
                prompt_topic = maybe
            continue

        # Shutdown noise
        if "shutting down" in s.lower():
            continue

        out.append(line)

    # Trim leading blanks
    while out and out[0].strip() == "":
        out.pop(0)

    # Put the topic at top (nice display)
    if prompt_topic:
        if not out or out[0].strip() != prompt_topic:
            out = [prompt_topic, ""] + out

    cleaned = "\n".join(out).strip()
    return cleaned + ("\n" if cleaned else "")


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
    # No auth here: harmless "service is up" message for browsers.
    return {"ok": True, "app": APP_NAME, "docs": "/docs", "hint": "Most endpoints require x-api-key. Try /health."}


@app.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    return {
        "ok": True,
        "app": APP_NAME,
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
    stdin = req.text.rstrip("\n") + "\n"
    res = await _run_process(_brain_repl_args(), stdin_text=stdin, timeout_s=req.timeout_s)
    res.answer = _clean_repl_stdout(res.stdout)
    return res


@app.post("/teach", response_model=RunResult)
async def teach(req: CommandRequest, request: Request, confirm: bool = Query(False)) -> RunResult:
    _require_auth(request)
    _require_confirm(confirm)
    line = req.line.strip()
    if not line:
        raise HTTPException(status_code=400, detail="Empty line.")
    stdin = line + "\n"
    return await _run_process(_brain_repl_args(), stdin_text=stdin, timeout_s=req.timeout_s)


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
