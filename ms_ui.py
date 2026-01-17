#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

APP_NAME = "MachineSpirit UI"
VERSION = "0.2.2"

# UI talks to the API service (default: localhost:8010)
MS_API_BASE = os.environ.get("MS_API_BASE", "http://127.0.0.1:8010").rstrip("/")
MS_API_KEY = os.environ.get("MS_API_KEY", "")

app = FastAPI(title=APP_NAME, version=VERSION)

# In-memory chat (LAN prototype)
# key = client ip, value = list[(role, text, ts)]
HISTORY: Dict[str, List[Tuple[str, str, float]]] = {}


def _client_id(req: Request) -> str:
    return (req.client.host if req.client else "unknown")


async def _api_post(path: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if MS_API_KEY:
        headers["x-api-key"] = MS_API_KEY
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{MS_API_BASE}{path}", headers=headers, json=json_data)
        r.raise_for_status()
        return r.json()


async def _api_get(path: str) -> Dict[str, Any]:
    headers: Dict[str, str] = {}
    if MS_API_KEY:
        headers["x-api-key"] = MS_API_KEY
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{MS_API_BASE}{path}", headers=headers)
        r.raise_for_status()
        return r.json()


def _escape(s: str) -> str:
    s = s or ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


_URL_RE = re.compile(r"(https?://[^\s<>()]+)")

def _linkify(escaped_text: str) -> str:
    # escaped_text is already HTML-escaped. We only turn URLs into <a> tags.
    def repl(m: re.Match) -> str:
        url = m.group(1)
        return f'<a href="{url}" target="_blank" rel="noreferrer">{url}</a>'
    return _URL_RE.sub(repl, escaped_text)


def _render_message(role: str, text: str, ts: float) -> str:
    who = "You" if role == "user" else "MachineSpirit"
    t = time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")

    safe = _escape(text)
    safe = _linkify(safe)

    # Preserve formatting (bullets/newlines) without turning it into a giant code block.
    # Looks like chat, but keeps readability.
    body_html = f'<div class="msg-text">{safe}</div>'

    if role == "user":
        return f"""
        <div class="row row-user">
          <div class="meta meta-user">{who} <span class="time">{t}</span></div>
          <div class="bubble bubble-user">{body_html}</div>
        </div>
        """
    else:
        return f"""
        <div class="row row-bot">
          <div class="avatar">MS</div>
          <div class="stack">
            <div class="meta meta-bot">{who} <span class="time">{t}</span></div>
            <div class="bubble bubble-bot">{body_html}</div>
          </div>
        </div>
        """


def _render_page(
    messages: List[Tuple[str, str, float]],
    theme_info: Dict[str, Any],
    notice: str = ""
) -> str:
    theme_name = (theme_info.get("theme") or "none")
    intensity = (theme_info.get("intensity") or "light")

    choices = theme_info.get("choices") or {}
    light_desc = (choices.get("light") or {}).get("desc", "Small flavor, very readable.")
    heavy_desc = (choices.get("heavy") or {}).get("desc", "More roleplay voice, more flavor.")

    msgs_html = "\n".join(_render_message(r, t, ts) for (r, t, ts) in messages[-80:])

    notice_html = ""
    if notice:
        notice_html = f'<div class="notice">{_escape(notice)}</div>'

    theme_badge = f"Theme: {_escape(theme_name)} ({_escape(intensity)})"

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MachineSpirit UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{
      --bg: #0b0b0c;
      --panel: rgba(18, 18, 22, 0.72);
      --panel2: rgba(18, 18, 22, 0.55);
      --line: rgba(255,255,255,0.08);
      --text: #f2f2f2;
      --muted: rgba(255,255,255,0.65);
      --user: #2a3b66;
      --bot: rgba(255,255,255,0.06);
      --shadow: 0 10px 28px rgba(0,0,0,0.45);
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background: radial-gradient(1200px 700px at 25% 0%, rgba(72,88,170,0.25), transparent 55%),
                  radial-gradient(900px 600px at 70% 10%, rgba(156,72,170,0.15), transparent 60%),
                  var(--bg);
      color: var(--text);
    }}

    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      background: rgba(10,10,12,0.72);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--line);
    }}

    .topbar {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 12px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}

    .brand {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .brand .title {{
      font-size: 18px;
      font-weight: 800;
      letter-spacing: 0.2px;
    }}
    .brand .sub {{
      font-size: 12px;
      color: var(--muted);
    }}

    .actions {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}

    .badge {{
      font-size: 12px;
      color: rgba(255,255,255,0.78);
      padding: 6px 10px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
      border-radius: 999px;
    }}

    .btn {{
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
      color: var(--text);
      padding: 7px 10px;
      border-radius: 10px;
      cursor: pointer;
      font-size: 12px;
      text-decoration: none;
    }}
    .btn:hover {{
      background: rgba(255,255,255,0.07);
    }}

    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 16px;
    }}

    .panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 16px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}

    details {{
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.02);
    }}
    summary {{
      cursor: pointer;
      padding: 12px 14px;
      font-weight: 700;
    }}
    .theme-box {{
      padding: 12px 14px 14px 14px;
      display: grid;
      grid-template-columns: 1fr 260px 200px;
      gap: 10px;
      align-items: end;
    }}
    .theme-box label {{
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    input[type="text"], select {{
      width: 100%;
      padding: 10px 10px;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: rgba(0,0,0,0.25);
      color: var(--text);
      outline: none;
    }}
    .theme-actions {{
      display: flex;
      gap: 8px;
    }}
    .hint {{
      font-size: 12px;
      color: var(--muted);
      margin-top: 10px;
      line-height: 1.35;
    }}
    .notice {{
      margin: 10px 14px 0 14px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.03);
      border-radius: 12px;
      color: rgba(255,255,255,0.9);
      font-size: 13px;
    }}

    .chat {{
      padding: 14px;
      max-height: calc(100vh - 250px);
      overflow: auto;
    }}

    .row {{
      display: flex;
      gap: 10px;
      margin: 10px 0;
      align-items: flex-start;
    }}

    .row-user {{
      justify-content: flex-end;
    }}

    .avatar {{
      width: 34px;
      height: 34px;
      border-radius: 999px;
      background: rgba(255,255,255,0.06);
      border: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      color: rgba(255,255,255,0.82);
      flex: 0 0 auto;
    }}

    .stack {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      max-width: 720px;
      width: 100%;
    }}

    .meta {{
      font-size: 11px;
      color: var(--muted);
      padding: 0 6px;
    }}
    .meta-user {{
      text-align: right;
    }}
    .time {{
      opacity: 0.7;
      margin-left: 6px;
    }}

    .bubble {{
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 12px;
      box-shadow: 0 8px 18px rgba(0,0,0,0.25);
    }}

    .bubble-user {{
      background: rgba(42, 59, 102, 0.75);
      max-width: 520px;
    }}

    .bubble-bot {{
      background: var(--bot);
      max-width: 720px;
    }}

    .msg-text {{
      white-space: pre-wrap;
      line-height: 1.42;
      font-size: 14px;
      color: rgba(255,255,255,0.92);
    }}
    .msg-text a {{
      color: #9fb4ff;
      text-decoration: none;
    }}
    .msg-text a:hover {{
      text-decoration: underline;
    }}

    .composer {{
      border-top: 1px solid var(--line);
      background: rgba(0,0,0,0.18);
      padding: 12px 14px;
      display: flex;
      gap: 10px;
      align-items: flex-end;
    }}

    textarea {{
      width: 100%;
      min-height: 46px;
      max-height: 140px;
      resize: vertical;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(0,0,0,0.25);
      color: var(--text);
      outline: none;
      font-size: 14px;
      line-height: 1.35;
    }}

    .send {{
      padding: 10px 14px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(120, 160, 255, 0.18);
      color: var(--text);
      cursor: pointer;
      font-weight: 700;
    }}
    .send:hover {{
      background: rgba(120, 160, 255, 0.25);
    }}

    .empty {{
      padding: 18px;
      color: var(--muted);
      text-align: center;
      border: 1px dashed rgba(255,255,255,0.12);
      border-radius: 16px;
      background: rgba(255,255,255,0.02);
    }}

    @media (max-width: 820px) {{
      .theme-box {{
        grid-template-columns: 1fr;
      }}
      .stack {{
        max-width: 100%;
      }}
      .bubble-user, .bubble-bot {{
        max-width: 100%;
      }}
      .chat {{
        max-height: calc(100vh - 340px);
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div class="brand">
        <div class="title">MachineSpirit UI</div>
        <div class="sub">LAN chat • API: {_escape(MS_API_BASE)}</div>
      </div>
      <div class="actions">
        <span class="badge">{theme_badge}</span>
        <a class="btn" href="/ui/reset">Reset chat</a>
        <a class="btn" href="/ui#theme">Theme settings</a>
      </div>
    </div>
  </header>

  <main>
    <div class="panel">
      <details id="theme">
        <summary>Theme settings</summary>
        <form method="post" action="/ui/theme">
          <div class="theme-box">
            <div>
              <label>Theme name</label>
              <input type="text" name="theme" value="{_escape(theme_name)}" placeholder="Warhammer 40k" />
              <div class="hint">Tip: you can also type <b>/theme</b> in chat.</div>
            </div>
            <div>
              <label>Intensity</label>
              <select name="intensity">
                <option value="light" {"selected" if intensity=="light" else ""}>1) Light — { _escape(light_desc) }</option>
                <option value="heavy" {"selected" if intensity=="heavy" else ""}>2) Heavy — { _escape(heavy_desc) }</option>
              </select>
            </div>
            <div class="theme-actions">
              <button class="btn" type="submit" name="action" value="save">Save theme</button>
              <button class="btn" type="submit" name="action" value="off">Disable theme</button>
            </div>
          </div>
        </form>
      </details>

      {notice_html}

      <div class="chat" id="chat">
        {msgs_html if msgs_html else '<div class="empty">Ask something like: <b>what is nat</b></div>'}
      </div>

      <form method="post" action="/ui/ask" class="composer" id="composer">
        <textarea name="text" id="msg" placeholder="Type a message… (Enter = send, Shift+Enter = new line)"></textarea>
        <button class="send" type="submit">Send</button>
      </form>
    </div>
  </main>

  <script>
    // Auto-scroll to bottom
    const chat = document.getElementById("chat");
    if (chat) chat.scrollTop = chat.scrollHeight;

    // Enter to send, Shift+Enter new line
    const ta = document.getElementById("msg");
    const form = document.getElementById("composer");
    if (ta && form) {{
      ta.addEventListener("keydown", (e) => {{
        if (e.key === "Enter" && !e.shiftKey) {{
          e.preventDefault();
          form.submit();
        }}
      }});
      ta.focus();
    }}
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return RedirectResponse(url="/ui/ask", status_code=302)


# If someone opens /ui/ask directly, show the UI (not “Method Not Allowed”).
@app.get("/ui/ask", response_class=HTMLResponse)
async def ui_get(request: Request) -> HTMLResponse:
    return RedirectResponse(url="/ui", status_code=302)


@app.get("/ui", response_class=HTMLResponse)
async def ui(request: Request) -> HTMLResponse:
    cid = _client_id(request)
    messages = HISTORY.get(cid, [])
    try:
        t = await _api_get("/theme")
    except Exception:
        t = {"theme": "none", "intensity": "light", "choices": {}}
    return HTMLResponse(_render_page(messages, t))


@app.get("/ui/reset", response_class=HTMLResponse)
async def ui_reset(request: Request) -> HTMLResponse:
    cid = _client_id(request)
    HISTORY[cid] = []
    return RedirectResponse(url="/ui", status_code=302)


@app.post("/ui/theme", response_class=HTMLResponse)
async def ui_theme(
    request: Request,
    theme: str = Form(""),
    intensity: str = Form("light"),
    action: str = Form("save"),
) -> HTMLResponse:
    cid = _client_id(request)
    messages = HISTORY.get(cid, [])

    notice = ""
    try:
        if action == "off":
            await _api_post("/theme", {"theme": "none", "intensity": "light"})
            notice = "Theme disabled."
        else:
            theme_clean = (theme or "").strip() or "none"
            intensity_clean = (intensity or "light").strip().lower()
            if intensity_clean not in ("light", "heavy"):
                intensity_clean = "light"
            await _api_post("/theme", {"theme": theme_clean, "intensity": intensity_clean})
            notice = f"Theme saved: {theme_clean} ({intensity_clean})."
    except Exception as e:
        notice = f"Theme update failed: {type(e).__name__}"

    try:
        t = await _api_get("/theme")
    except Exception:
        t = {"theme": "none", "intensity": "light", "choices": {}}

    return HTMLResponse(_render_page(messages, t, notice=notice))


@app.post("/ui/ask", response_class=HTMLResponse)
async def ui_ask(request: Request, text: str = Form("")) -> HTMLResponse:
    cid = _client_id(request)
    messages = HISTORY.setdefault(cid, [])

    text = (text or "").strip()
    if not text:
        return RedirectResponse(url="/ui", status_code=302)

    messages.append(("user", text, time.time()))
    notice = ""

    try:
        data = await _api_post("/ask", {"text": text})
        answer = (data.get("answer") or "").strip() or "(no answer returned)"
        messages.append(("bot", answer, time.time()))

        # Keep UI header theme accurate
        t = data.get("theme") or {}
        if not isinstance(t, dict):
            t = {}
        # If API didn’t include choices, refresh them once
        if "choices" not in t:
            try:
                full = await _api_get("/theme")
                t["choices"] = full.get("choices") or {}
            except Exception:
                t["choices"] = {}
    except Exception as e:
        messages.append(("bot", f"(API error: {type(e).__name__})", time.time()))
        t = {"theme": "none", "intensity": "light", "choices": {}}
        notice = "API call failed. Check machinespirit-api.service."

    # cap history per client
    if len(messages) > 120:
        HISTORY[cid] = messages[-120:]

    return HTMLResponse(_render_page(HISTORY[cid], t, notice=notice))


# Handy JSON endpoints on the UI service (for quick tests)
@app.get("/api/meta")
async def api_meta() -> JSONResponse:
    return JSONResponse({"ok": True, "ui": "machinespirit-ui", "version": VERSION, "api_base": MS_API_BASE})


@app.get("/api/theme")
async def api_theme() -> JSONResponse:
    try:
        t = await _api_get("/theme")
        return JSONResponse(t)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"})


@app.post("/api/ask")
async def api_ask_proxy(payload: Dict[str, Any]) -> JSONResponse:
    data = await _api_post("/ask", payload)
    return JSONResponse(data)
