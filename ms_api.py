#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from contextlib import contextmanager

import fcntl
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field


APP_NAME = "MachineSpirit API"
APP_VERSION = "0.3.4"

REPO_DIR = Path(__file__).resolve().parent
BRAIN_PATH = REPO_DIR / "brain.py"

# Use system python for brain.py on purpose (stable + predictable)
PYTHON_BIN = os.getenv("MS_BRAIN_PYTHON", "/usr/bin/python3")

LOCK_PATH = Path(os.getenv("MS_LOCK_PATH", str(REPO_DIR / ".machinespirit.lock")))

API_KEY_NAME = "x-api-key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


# --- Models

class AskRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    timeout_s: float = Field(15.0, ge=0.5, le=120.0)
    debug: bool = False


class AskResponse(BaseModel):
    ok: bool
    topic: str
    answer: str
    duration_s: float
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None  # only when debug=True


class RunResponse(BaseModel):
    ok: bool
    duration_s: float
    exit_code: int
    stdout: str = ""
    stderr: str = ""


# --- Auth

def _expected_api_key() -> str:
    return (os.getenv("MS_API_KEY") or "").strip()


def _require_auth(api_key: Optional[str]) -> None:
    expected = _expected_api_key()
    # If no key is configured, allow requests (dev mode).
    if not expected:
        return
    if not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized (missing/invalid x-api-key).")


# --- Locking

@contextmanager
def _acquire_lock(timeout_s: float = 10.0):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_PATH, "a+")
    start = time.time()
    waited = 0.0
    try:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                waited = time.time() - start
                if waited >= timeout_s:
                    raise HTTPException(status_code=503, detail=f"Busy (lock wait > {timeout_s:.1f}s)")
                time.sleep(0.05)
        yield waited
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            f.close()
        except Exception:
            pass


# --- Process runner

async def _run_process(args, stdin_text: Optional[str] = None, timeout_s: Optional[float] = None) -> RunResponse:
    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        data_in = (stdin_text or "").encode("utf-8")
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(input=data_in), timeout=timeout_s)
        code = int(proc.returncode or 0)
        return RunResponse(
            ok=(code == 0),
            duration_s=(time.time() - t0),
            exit_code=code,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        return RunResponse(ok=False, duration_s=(time.time() - t0), exit_code=124, stdout="", stderr="Timeout")


def _brain_repl_args():
    return [PYTHON_BIN, str(BRAIN_PATH)]


# --- Query normalization + output cleaning

def _normalize_query(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r"\s+", " ", q)
    q = q.strip(" \t\r\n?.!")
    low = q.lower()

    prefixes = [
        "what is ",
        "whats ",
        "what's ",
        "define ",
        "explain ",
        "tell me about ",
        "meaning of ",
        "what does ",
        "what are ",
    ]
    for p in prefixes:
        if low.startswith(p):
            q = q[len(p):].strip()
            break

    return q


def _parse_alias_suggestion(clean_answer: str) -> Optional[str]:
    # "Suggestion: /alias what is nat|nat"
    m = re.search(r"Suggestion:\s*/alias\s+.+\|([A-Za-z0-9 _./:-]+)\s*$", clean_answer.strip(), flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()


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

    prompt_re = re.compile(r"^\s*>\s*(.*)$")

    lines = raw.splitlines()
    topic = ""
    body = []
    seen_topic_prompt = False

    for line in lines:
        s = line.strip()

        # Drop banner
        if s.startswith("Machine Spirit brain online."):
            continue

        # Detect prompt lines
        pm = prompt_re.match(line)
        if pm:
            prompt_text = (pm.group(1) or "").strip()

            # Never include shutdown prompt
            if "shutting down" in prompt_text.lower():
                break

            # First prompt becomes the topic header
            if not seen_topic_prompt and prompt_text:
                topic = prompt_text
                seen_topic_prompt = True
                continue

            # Any later prompt means we reached end of answer
            break

        # Never include any stray "Shutting down." lines
        if "shutting down" in s.lower():
            continue

        body.append(line.rstrip())

    while body and body[0].strip() == "":
        body.pop(0)

    body_text = "\n".join(body).strip()

    if topic and body_text:
        return f"{topic}\n\n{body_text}"
    if topic:
        return topic
    return body_text


# --- Clean fallback answers (so users don’t see RFC boilerplate)

_STARTER_KB: Dict[str, str] = {
    "nat": (
        "NAT (Network Address Translation)\n\n"
        "Definition:\n"
        "- A router feature that translates private IP addresses (like 192.168.x.x) to a public IP address so multiple devices can share one internet connection.\n\n"
        "Key points:\n"
        "- Very common in home networks.\n"
        "- Helps conserve public IPv4 addresses.\n"
        "- Not a firewall by itself, but it can reduce unsolicited inbound connections.\n\n"
        "Examples:\n"
        "- Your PC is 192.168.1.25, but websites see your router’s public IP.\n\n"
        "Sources:\n"
        "- RFC 3022 (Traditional NAT): https://www.rfc-editor.org/rfc/rfc3022\n"
    ),
    "subnet mask": (
        "Subnet Mask\n\n"
        "Definition:\n"
        "- A 32-bit value (example: 255.255.255.0) that splits an IPv4 address into the network part and the host part.\n\n"
        "Key points:\n"
        "- Works with an IP address to determine what subnet it belongs to.\n"
        "- The prefix length is the same idea: /24 = 255.255.255.0.\n"
        "- Used for routing and subnetting.\n\n"
        "Examples:\n"
        "- IP: 192.168.1.50, Mask: 255.255.255.0 → Network: 192.168.1.0/24\n"
        "- /26 means 64 addresses per subnet (62 usable hosts usually).\n"
    ),
}


def _count_section_bullets(text: str, section_name: str) -> int:
    # counts "- ..." lines under a section header like "Key points:" until next header
    pat = re.compile(rf"(?ims)^{re.escape(section_name)}:\s*\n(.*?)(?=^\w[\w ]*:\s*$|\Z)")
    m = pat.search(text)
    if not m:
        return 0
    block = m.group(1)
    return len(re.findall(r"(?m)^\s*-\s+\S", block))


def _definition_first_bullet(text: str) -> str:
    m = re.search(r"(?ims)^definition:\s*\n-\s*(.+)$", text)
    return (m.group(1).strip() if m else "")


def _looks_low_quality(ans: str) -> bool:
    t = (ans or "").strip()
    if not t:
        return True

    low = t.lower()
    score = 0

    # “NAT may refer to…” / disambiguation = almost always garbage for our use
    if "may refer to" in low:
        score += 3

    # RFC boilerplate phrases we don’t want as “answers”
    boiler = [
        "abstract this memo",
        "this memo is intended",
        "this memo clarifies",
        "does not specify an internet standard",
        "informational companion",
        "std ",
    ]
    if any(b in low for b in boiler):
        score += 2

    # weak definition
    d1 = _definition_first_bullet(t)
    if d1 and len(d1) < 35:
        score += 2

    # key points section but basically empty
    if re.search(r"(?im)^key points:\s*$", t) and _count_section_bullets(t, "Key points") < 2:
        score += 2

    # sources only wikipedia (common junk signal)
    if "sources:" in low and "wikipedia" in low and "rfc-editor" not in low:
        score += 1

    return score >= 2


def _polish_answer(topic: str, ans: str) -> str:
    if not ans:
        return ans

    tkey = topic.strip().lower()

    # If it’s junk, replace with our clean fallback answer (if we have it)
    if _looks_low_quality(ans) and tkey in _STARTER_KB:
        return _STARTER_KB[tkey]

    # If it’s junk and we DON’T have a fallback, return a clean “not learned yet” message
    if _looks_low_quality(ans) and tkey not in _STARTER_KB:
        return (
            f"{topic.upper()}\n\n"
            "I don’t have a clean learned answer for this yet.\n\n"
            "Try one of these:\n"
            f"- Ask a shorter topic (example: \"{tkey}\")\n"
            f"- Teach it later using your /teach flow\n"
        )

    # Otherwise keep answer, but trim a little obvious noise
    drop_contains = [
        "table of contents",
        "copyright notice",
        "this memo does not specify",
        "page ",
    ]

    out_lines = []
    for line in ans.splitlines():
        low = line.lower()
        if any(d in low for d in drop_contains):
            continue
        out_lines.append(line.rstrip())

    text = "\n".join(out_lines).strip()

    # Keep UI readable
    if len(text) > 4000:
        text = text[:4000].rstrip() + "\n\n(Trimmed for display.)"

    return text


# --- FastAPI app

app = FastAPI(title=APP_NAME, version=APP_VERSION)


@app.get("/")
async def root():
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION}


@app.get("/health")
async def health(request: Request, api_key: str | None = Security(api_key_header)):
    _require_auth(api_key)
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "brain_path": str(BRAIN_PATH),
        "repo_dir": str(REPO_DIR),
        "python": PYTHON_BIN,
        "lock_path": str(LOCK_PATH),
        "lock_wait_s": 0.0,
    }


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request, api_key: str | None = Security(api_key_header)):
    _require_auth(api_key)

    topic_in = _normalize_query(req.text)
    if not topic_in:
        raise HTTPException(status_code=422, detail="text is required")

    t0 = time.time()
    lock_wait = 0.0

    # 1) ask the brain once
    with _acquire_lock(timeout_s=10.0) as waited:
        lock_wait = waited
        raw1 = await _run_process(_brain_repl_args(), stdin_text=topic_in + "\n", timeout_s=req.timeout_s)

    clean1 = _clean_repl_stdout(raw1.stdout)
    sug = _parse_alias_suggestion(clean1)

    # 2) if it suggested an alias, follow it once (but do NOT write aliases automatically)
    if sug and sug.lower() != topic_in.lower():
        with _acquire_lock(timeout_s=10.0):
            raw2 = await _run_process(_brain_repl_args(), stdin_text=sug + "\n", timeout_s=req.timeout_s)
        clean2 = _clean_repl_stdout(raw2.stdout)
        final_topic = sug
        final_answer = clean2
        raw_final = raw2
    else:
        final_topic = topic_in
        final_answer = clean1
        raw_final = raw1

    final_answer = _polish_answer(final_topic, final_answer)

    resp = AskResponse(
        ok=True,
        topic=final_topic,
        answer=final_answer,
        duration_s=(time.time() - t0),
        error=None,
        raw=None,
    )

    if req.debug:
        resp.raw = {
            "topic_in": topic_in,
            "suggested_topic": sug,
            "lock_wait_s": lock_wait,
            "brain_exit_code": raw_final.exit_code if raw_final else None,
            "stdout": raw_final.stdout if raw_final else "",
            "stderr": raw_final.stderr if raw_final else "",
        }

    return resp
