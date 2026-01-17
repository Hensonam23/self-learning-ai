#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse


APP_NAME = "MachineSpirit UI"
VERSION = "0.2.0"

MS_API_BASE = os.environ.get("MS_API_BASE", "http://127.0.0.1:8010").rstrip("/")
MS_API_KEY = os.environ.get("MS_API_KEY", "")

app = FastAPI(title=APP_NAME, version=VERSION)

# in-memory chat history (simple + good enough for LAN prototype)
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
    headers = {}
    if MS_API_KEY:
        headers["x-api-key"] = MS_API_KEY
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{MS_API_BASE}{path}", headers=headers)
        r.raise_for_status()
        return r.json()


def _escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_page(messages: List[Tuple[str, str, float]], theme_info: Dict[str, str], notice: str = "") -> str:
    theme_name = theme_info.get("theme", "none")
    intensity = theme_info.get("intensity", "light")

    rows = []
    for role, text, ts in messages[-30:]:
        who = "You" if role == "user" else "MachineSpirit"
        bubble_class = "bubble user" if role == "user" else "bubble bot"
        rows.append(
            f"""
            <div class="msg { 'right' if role=='user' else 'left' }">
              <div class="{bubble_class}">
                <div class="who">{_escape(who)}</div>
                <pre>{_escape(text)}</pre>
              </div>
            </div>
            """
        )

    notice_html = f'<div class="notice">{_escape(notice)}</div>' if notice else ""

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MachineSpirit UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{
      --bg: #0b0b0c;
      --panel: #121215;
      --panel2: #17171b;
      --border: #22242a;
      --text: #f2f2f2;
      --muted: #a8a8b3;
      --user: #2a65ff;
      --bot: #202228;
    }}

    body {{
      margin: 0;
      background: radial-gradient(1200px 600px at 20% 0%, #141427, var(--bg));
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    }}

    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      padding: 14px 16px;
      background: rgba(18,18,21,0.92);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--border);
    }}

    .title {{
      font-size: 18px;
      font-weight: 800;
      letter-spacing: 0.2px;
    }}

    .sub {{
      margin-top: 4px;
      font-size: 12px;
      color: var(--muted);
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }}

    .pill {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
      padding: 4px 10px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 999px;
    }}

    .wrap {{
      max-width: 980px;
      margin: 0 auto;
      padding: 16px;
    }}

    .card {{
      background: rgba(18,18,21,0.85);
      border: 1px solid var(--border);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0,0,0,0.35);
    }}

    .toolbar {{
      padding: 12px 12px 0 12px;
    }}

    details {{
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 12px;
      padding: 10px 12px;
    }}

    summary {{
      cursor: pointer;
      font-weight: 700;
    }}

    .settings {{
      margin-top: 10px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }}

    .settings label {{
      font-size: 12px;
      color: var(--muted);
      display: block;
      margin-bottom: 6px;
    }}

    .settings input, .settings select {{
      width: 100%;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(0,0,0,0.25);
      color: var(--text);
      outline: none;
    }}

    .settings .row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}

    .btn {{
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.08);
      color: var(--text);
      cursor: pointer;
      font-weight: 700;
    }}

    .btn.primary {{
      background: rgba(42,101,255,0.25);
      border-color: rgba(42,101,255,0.5);
    }}

    .notice {{
      margin: 12px;
      padding: 10px 12px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.05);
      border-radius: 12px;
      color: var(--muted);
      font-size: 13px;
    }}

    .chat {{
      height: calc(100vh - 290px);
      min-height: 360px;
      max-height: 640px;
      overflow-y: auto;
      padding: 14px 14px 6px 14px;
      background: linear-gradient(180deg, rgba(0,0,0,0.04), rgba(0,0,0,0.10));
    }}

    .msg {{
      display: flex;
      margin: 10px 0;
    }}

    .msg.left {{ justify-content: flex-start; }}
    .msg.right {{ justify-content: flex-end; }}

    .bubble {{
      max-width: 78%;
      border-radius: 16px;
      padding: 10px 12px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.04);
    }}

    .bubble.user {{
      background: rgba(42,101,255,0.20);
      border-color: rgba(42,101,255,0.35);
    }}

    .bubble.bot {{
      background: rgba(32,34,40,0.60);
      border-color: rgba(255,255,255,0.08);
    }}

    .who {{
      font-size: 11px;
      color: rgba(255,255,255,0.75);
      margin-bottom: 6px;
      font-weight: 700;
      letter-spacing: 0.2px;
    }}

    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-wrap: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 13px;
      line-height: 1.35;
    }}

    .inputbar {{
      padding: 12px;
      border-top: 1px solid var(--border);
      background: rgba(18,18,21,0.92);
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }}

    textarea {{
      resize: none;
      height: 52px;
      padding: 12px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(0,0,0,0.25);
      color: var(--text);
      outline: none;
      font-size: 14px;
    }}

    .hint {{
      margin-top: 6px;
      font-size: 12px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <header>
    <div class="title">MachineSpirit</div>
    <div class="sub">
      <span class="pill">UI: {VERSION}</span>
      <span class="pill">API: {MS_API_BASE}</span>
      <span class="pill">Theme: {_escape(theme_name)} ({_escape(intensity)})</span>
      <span class="pill"><a href="/ui/reset" style="color: #c9c9ff; text-decoration: none;">Reset chat</a></span>
    </div>
  </header>

  <div class="wrap">
    <div class="card">
      <div class="toolbar">
        <details>
          <summary>Theme settings</summary>
          <div class="settings">
            <form method="post" action="/ui/theme">
              <div class="row">
                <div>
                  <label>Theme name</label>
                  <input name="theme" placeholder="Warhammer 40k" value="{_escape(theme_name if theme_name!='none' else '')}" />
                </div>
                <div>
                  <label>Intensity</label>
                  <select name="intensity">
                    <option value="light" {"selected" if intensity=="light" else ""}>1) Light — Small flavor, very readable</option>
                    <option value="heavy" {"selected" if intensity=="heavy" else ""}>2) Heavy — More roleplay voice, still clear</option>
                  </select>
                </div>
              </div>
              <div style="margin-top: 10px; display:flex; gap:10px; flex-wrap: wrap;">
                <button class="btn primary" type="submit">Save theme</button>
                <button class="btn" type="submit" name="theme" value="none">Disable theme</button>
              </div>
              <div class="hint">
                Tip: you can also type <b>/theme</b> or <b>/theme set Warhammer 40k light</b> directly in chat.
              </div>
            </form>
          </div>
        </details>
      </div>

      {notice_html}

      <div class="chat" id="chat">
        {''.join(rows) if rows else '<div style="color: var(--muted); padding: 10px;">Ask something like: <b>what is nat</b></div>'}
      </div>

      <form class="inputbar" method="post" action="/ui/ask">
        <textarea name="text" placeholder="Ask a question... (example: what is nat)"></textarea>
        <button class="btn primary" type="submit">Send</button>
      </form>
    </div>
  </div>

  <script>
    // auto-scroll to bottom
    const chat = document.getElementById("chat");
    chat.scrollTop = chat.scrollHeight;
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return RedirectResponse(url="/ui", status_code=302)


@app.get("/ui", response_class=HTMLResponse)
async def ui(request: Request) -> HTMLResponse:
    cid = _client_id(request)
    messages = HISTORY.get(cid, [])
    try:
        t = await _api_get("/theme")
        theme_info = {"theme": t.get("theme", "none"), "intensity": t.get("intensity", "light")}
    except Exception:
        theme_info = {"theme": "none", "intensity": "light"}

    return HTMLResponse(_render_page(messages, theme_info))


@app.get("/ui/reset", response_class=HTMLResponse)
async def ui_reset(request: Request) -> HTMLResponse:
    cid = _client_id(request)
    HISTORY[cid] = []
    return RedirectResponse(url="/ui", status_code=302)


@app.post("/ui/theme", response_class=HTMLResponse)
async def ui_theme(request: Request, theme: str = Form(""), intensity: str = Form("light")) -> HTMLResponse:
    cid = _client_id(request)
    messages = HISTORY.get(cid, [])

    theme = (theme or "").strip()
    if theme == "":
        theme = "none"

    notice = ""
    try:
        await _api_post("/theme", {"theme": theme, "intensity": intensity})
        notice = f"Theme saved: {theme} ({intensity})"
    except Exception as e:
        notice = f"Theme save failed: {type(e).__name__}"

    try:
        t = await _api_get("/theme")
        theme_info = {"theme": t.get("theme", "none"), "intensity": t.get("intensity", "light")}
    except Exception:
        theme_info = {"theme": "none", "intensity": "light"}

    return HTMLResponse(_render_page(messages, theme_info, notice=notice))


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
        answer = (data.get("answer") or "").strip()
        if not answer:
            answer = "(no answer returned)"
        messages.append(("bot", answer, time.time()))
        theme_info = data.get("theme") or {}
        theme_info = {"theme": theme_info.get("theme", "none"), "intensity": theme_info.get("intensity", "light")}
    except Exception as e:
        messages.append(("bot", f"(API error: {type(e).__name__})", time.time()))
        theme_info = {"theme": "none", "intensity": "light"}
        notice = "API call failed. Check machinespirit-api.service."

    # keep last 60 lines max
    if len(messages) > 60:
        HISTORY[cid] = messages[-60:]

    return HTMLResponse(_render_page(HISTORY[cid], theme_info, notice=notice))


# JSON proxy endpoint (handy for testing UI server itself)
@app.post("/api/ask")
async def api_ask_proxy(payload: Dict[str, Any]) -> JSONResponse:
    data = await _api_post("/ask", payload)
    return JSONResponse(data)
