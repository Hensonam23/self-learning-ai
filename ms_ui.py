#!/usr/bin/env python3
import os
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

APP_TITLE = "MachineSpirit UI"
DEFAULT_API_BASE = "http://127.0.0.1:8010"

def _load_env_file(path: Path) -> Dict[str, str]:
    """
    Minimal KEY=VALUE loader for ~/.config/machinespirit/api.env
    - ignores blank lines and comments
    - supports quoted values
    """
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out

# Load config from env first, fall back to api.env
ENV_PATH = Path.home() / ".config" / "machinespirit" / "api.env"
FILE_ENV = _load_env_file(ENV_PATH)

MS_API_BASE = os.environ.get("MS_API_BASE") or FILE_ENV.get("MS_API_BASE") or DEFAULT_API_BASE
MS_API_KEY = os.environ.get("MS_API_KEY") or FILE_ENV.get("MS_API_KEY") or ""

# If you forget the key, we want a clear error in the UI instead of silent failure.
if not MS_API_KEY:
    print("WARNING: MS_API_KEY is not set (env or ~/.config/machinespirit/api.env). UI will not be able to call the API.")

API_TIMEOUT_S = float(os.environ.get("MS_UI_TIMEOUT_S") or "25")

app = FastAPI(title=APP_TITLE, version="0.2.0")

class AskIn(BaseModel):
    text: str

def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )

def _linkify(text: str) -> str:
    # Turn bare URLs into <a> links (simple + safe)
    url_re = re.compile(r'(https?://[^\s<>"\']+)')
    def repl(m: re.Match) -> str:
        u = m.group(1)
        safe = _html_escape(u)
        return f'<a href="{safe}" target="_blank" rel="noopener noreferrer">{safe}</a>'
    return url_re.sub(repl, text)

def _format_answer_to_html(answer: str) -> str:
    """
    Pretty-print plaintext answers into readable HTML:
    - escape html
    - linkify
    - preserve paragraphs + bullets
    """
    if not answer:
        return "<div class='muted'>No answer returned.</div>"

    s = answer.strip()
    s = _html_escape(s)

    # Convert simple bullets into <ul>
    lines = s.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:])
                i += 1
            li = "".join([f"<li>{_linkify(it)}</li>" for it in items])
            blocks.append(f"<ul>{li}</ul>")
            continue

        # blank line => paragraph break
        if line.strip() == "":
            blocks.append("")
            i += 1
            continue

        # normal text line
        blocks.append(_linkify(line))
        i += 1

    # Rebuild with paragraph breaks, keep single newlines inside paragraphs
    html_parts = []
    para = []
    for b in blocks:
        if b == "":
            if para:
                html_parts.append("<p>" + "<br/>".join(para) + "</p>")
                para = []
            continue
        # already a <ul>
        if b.startswith("<ul>"):
            if para:
                html_parts.append("<p>" + "<br/>".join(para) + "</p>")
                para = []
            html_parts.append(b)
        else:
            para.append(b)

    if para:
        html_parts.append("<p>" + "<br/>".join(para) + "</p>")

    return "\n".join(html_parts)

async def _call_api(text: str) -> Dict[str, Any]:
    if not MS_API_KEY:
        raise HTTPException(status_code=500, detail="MS_API_KEY is missing on the UI server. Set it in ~/.config/machinespirit/api.env or the service Environment.")

    url = MS_API_BASE.rstrip("/") + "/ask"
    payload = {"text": text}

    t0 = time.time()
    async with httpx.AsyncClient(timeout=API_TIMEOUT_S) as client:
        r = await client.post(
            url,
            headers={"x-api-key": MS_API_KEY, "Content-Type": "application/json"},
            json=payload,
        )
    dt = time.time() - t0

    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Unauthorized (bad x-api-key).")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=f"API error {r.status_code}: {r.text[:200]}")

    data = r.json()
    data["duration_s_ui"] = dt
    return data

CHAT_HTML = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{APP_TITLE}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{
      --bg0: #07070a;
      --bg1: #0b0b10;
      --card: rgba(18, 18, 24, 0.72);
      --card2: rgba(18, 18, 24, 0.92);
      --border: rgba(255,255,255,0.08);
      --text: #f2f2f5;
      --muted: rgba(242,242,245,0.62);
      --accent: #6ea8ff;
      --accent2: #8a5cff;
      --user: rgba(48, 88, 160, 0.52);
      --user2: rgba(88, 60, 180, 0.52);
      --shadow: 0 18px 45px rgba(0,0,0,0.55);
    }}

    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background:
        radial-gradient(900px 400px at 20% 5%, rgba(110,168,255,0.14), transparent 60%),
        radial-gradient(900px 400px at 80% 5%, rgba(138,92,255,0.12), transparent 60%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
    }}

    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      backdrop-filter: blur(10px);
      background: rgba(10,10,14,0.72);
      border-bottom: 1px solid var(--border);
    }}

    .topbar {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 14px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }}

    .brand {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .title {{
      font-size: 18px;
      font-weight: 750;
      letter-spacing: 0.2px;
    }}
    .sub {{
      font-size: 12px;
      color: var(--muted);
    }}

    .btn {{
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.04);
      color: var(--text);
      padding: 8px 10px;
      border-radius: 10px;
      cursor: pointer;
      transition: transform 0.06s ease, background 0.2s ease;
      font-size: 13px;
    }}
    .btn:hover {{ background: rgba(255,255,255,0.07); }}
    .btn:active {{ transform: scale(0.98); }}

    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 18px 16px 110px;
    }}

    .chat {{
      display: flex;
      flex-direction: column;
      gap: 14px;
      min-height: calc(100vh - 190px);
    }}

    .row {{
      display: flex;
      align-items: flex-end;
      gap: 10px;
    }}
    .row.assistant {{ justify-content: flex-start; }}
    .row.user {{ justify-content: flex-end; }}

    .avatar {{
      width: 32px;
      height: 32px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      font-weight: 800;
      font-size: 12px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.04);
      flex: 0 0 auto;
    }}
    .avatar.ms {{
      background: linear-gradient(135deg, rgba(110,168,255,0.18), rgba(138,92,255,0.14));
    }}
    .avatar.you {{
      background: linear-gradient(135deg, rgba(110,168,255,0.22), rgba(138,92,255,0.20));
    }}

    .bubble {{
      max-width: min(720px, 82vw);
      border-radius: 18px;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      overflow: hidden;
    }}

    .bubble .meta {{
      padding: 10px 14px 0;
      font-size: 12px;
      color: var(--muted);
    }}

    .bubble .content {{
      padding: 12px 14px 14px;
      font-size: 14px;
      line-height: 1.45;
    }}

    .bubble.assistant {{
      background: var(--card);
    }}

    .bubble.user {{
      background: linear-gradient(135deg, var(--user), var(--user2));
    }}

    .bubble.user .meta {{
      color: rgba(255,255,255,0.72);
    }}

    .content p {{
      margin: 0 0 10px;
    }}
    .content p:last-child {{
      margin-bottom: 0;
    }}
    .content ul {{
      margin: 8px 0 10px 18px;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .muted {{
      color: var(--muted);
    }}

    .composer {{
      position: fixed;
      left: 0; right: 0; bottom: 0;
      padding: 14px 16px;
      border-top: 1px solid var(--border);
      backdrop-filter: blur(10px);
      background: rgba(10,10,14,0.75);
    }}

    .composer-inner {{
      max-width: 1100px;
      margin: 0 auto;
      display: flex;
      gap: 10px;
      align-items: flex-end;
    }}

    textarea {{
      width: 100%;
      min-height: 46px;
      max-height: 180px;
      resize: vertical;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.03);
      color: var(--text);
      padding: 12px 12px;
      outline: none;
      font-size: 14px;
      line-height: 1.35;
    }}

    .send {{
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: linear-gradient(135deg, rgba(110,168,255,0.22), rgba(138,92,255,0.22));
      color: var(--text);
      cursor: pointer;
      font-weight: 700;
      min-width: 86px;
    }}
    .send:disabled {{
      opacity: 0.55;
      cursor: not-allowed;
    }}

    .hint {{
      max-width: 1100px;
      margin: 10px auto 0;
      color: var(--muted);
      font-size: 12px;
      padding: 0 16px 10px;
    }}

    @media (max-width: 560px) {{
      .bubble {{ max-width: 88vw; }}
      .avatar {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div class="brand">
        <div class="title">MachineSpirit UI</div>
        <div class="sub">LAN chat • API: {MS_API_BASE}</div>
      </div>
      <button class="btn" id="clearBtn" title="Clear saved chat">Clear chat</button>
    </div>
  </header>

  <div class="wrap">
    <div class="chat" id="chat"></div>
  </div>

  <div class="composer">
    <div class="composer-inner">
      <textarea id="msg" placeholder="Type a message... (Enter = send, Shift+Enter = new line)"></textarea>
      <button class="send" id="sendBtn">Send</button>
    </div>
    <div class="hint">Tip: If answers are bad, the brain might not have learned it yet (that’s a later step). The UI is just the shell.</div>
  </div>

<script>
(function() {{
  const chatEl = document.getElementById("chat");
  const msgEl = document.getElementById("msg");
  const sendBtn = document.getElementById("sendBtn");
  const clearBtn = document.getElementById("clearBtn");

  const STORE_KEY = "machinespirit_chat_v2";

  function loadChat() {{
    try {{
      return JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
    }} catch (e) {{
      return [];
    }}
  }}

  function saveChat(items) {{
    localStorage.setItem(STORE_KEY, JSON.stringify(items.slice(-200)));
  }}

  function scrollToBottom() {{
    window.scrollTo({{ top: document.body.scrollHeight, behavior: "smooth" }});
  }}

  function addMessage(role, label, htmlContent) {{
    const row = document.createElement("div");
    row.className = "row " + (role === "user" ? "user" : "assistant");

    const avatar = document.createElement("div");
    avatar.className = "avatar " + (role === "user" ? "you" : "ms");
    avatar.textContent = role === "user" ? "Y" : "MS";

    const bubble = document.createElement("div");
    bubble.className = "bubble " + (role === "user" ? "user" : "assistant");

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = label;

    const content = document.createElement("div");
    content.className = "content";
    content.innerHTML = htmlContent;

    bubble.appendChild(meta);
    bubble.appendChild(content);

    if (role === "user") {{
      row.appendChild(bubble);
      row.appendChild(avatar);
    }} else {{
      row.appendChild(avatar);
      row.appendChild(bubble);
    }}

    chatEl.appendChild(row);
    scrollToBottom();
    return {{ row, content }};
  }}

  function escapeHtml(s) {{
    return s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
  }}

  function linkify(s) {{
    return s.replace(/(https?:\\/\\/[^\\s<>"']+)/g, (m) => {{
      const safe = escapeHtml(m);
      return `<a href="${{safe}}" target="_blank" rel="noopener noreferrer">${{safe}}</a>`;
    }});
  }}

  function prettyText(s) {{
    const raw = (s || "").trim();
    if (!raw) return "<div class='muted'>No answer returned.</div>";

    const esc = escapeHtml(raw);
    const lines = esc.split("\\n");

    // Build simple paragraphs + bullet lists
    let html = "";
    let inList = false;

    function closeList() {{
      if (inList) {{
        html += "</ul>";
        inList = false;
      }}
    }}

    for (let i = 0; i < lines.length; i++) {{
      const line = lines[i];
      const t = line.trim();

      if (!t) {{
        closeList();
        html += "<p></p>";
        continue;
      }}

      if (t.startsWith("- ")) {{
        if (!inList) {{
          html += "<ul>";
          inList = true;
        }}
        html += "<li>" + linkify(t.slice(2)) + "</li>";
        continue;
      }}

      closeList();
      html += "<p>" + linkify(line) + "</p>";
    }}

    closeList();
    return html;
  }}

  function render() {{
    chatEl.innerHTML = "";
    const items = loadChat();
    for (const it of items) {{
      addMessage(it.role, it.label, it.html);
    }}
    scrollToBottom();
  }}

  async function send() {{
    const text = (msgEl.value || "").trim();
    if (!text) return;

    msgEl.value = "";
    sendBtn.disabled = true;

    const items = loadChat();

    // user message
    const userHtml = "<p>" + linkify(escapeHtml(text)) + "</p>";
    addMessage("user", "You", userHtml);
    items.push({{ role: "user", label: "You", html: userHtml }});
    saveChat(items);

    // typing placeholder
    const placeholder = addMessage("assistant", "MachineSpirit", "<div class='muted'>Thinking...</div>");

    try {{
      const res = await fetch("/api/ask", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ text }})
      }});

      const data = await res.json();

      if (!res.ok || !data.ok) {{
        const err = (data && (data.error || data.detail)) || ("HTTP " + res.status);
        const errHtml = "<p><b>Error:</b> " + escapeHtml(String(err)) + "</p>";
        placeholder.content.innerHTML = errHtml;
        items.push({{ role: "assistant", label: "MachineSpirit", html: errHtml }});
        saveChat(items);
        return;
      }}

      const answerHtml = prettyText(data.answer || "");
      placeholder.content.innerHTML = answerHtml;

      items.push({{ role: "assistant", label: "MachineSpirit", html: answerHtml }});
      saveChat(items);

    }} catch (e) {{
      const errHtml = "<p><b>Error:</b> " + escapeHtml(String(e)) + "</p>";
      placeholder.content.innerHTML = errHtml;
      items.push({{ role: "assistant", label: "MachineSpirit", html: errHtml }});
      saveChat(items);
    }} finally {{
      sendBtn.disabled = false;
      msgEl.focus();
      scrollToBottom();
    }}
  }}

  sendBtn.addEventListener("click", send);

  msgEl.addEventListener("keydown", (e) => {{
    if (e.key === "Enter" && !e.shiftKey) {{
      e.preventDefault();
      send();
    }}
  }});

  clearBtn.addEventListener("click", () => {{
    localStorage.removeItem(STORE_KEY);
    render();
  }});

  render();
  msgEl.focus();
}})();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(CHAT_HTML)

# Keep this path because you’ve been using it already.
@app.get("/ui/ask", response_class=HTMLResponse)
async def ui_ask():
    return HTMLResponse(CHAT_HTML)

# The UI calls this (JSON only).
@app.post("/api/ask")
async def api_ask(req: AskIn):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")

    data = await _call_api(text)
    # Normalize output for the UI
    return {
        "ok": True,
        "topic": data.get("topic") or "",
        "answer": data.get("answer") or "",
        "duration_s": float(data.get("duration_s") or 0.0),
        "error": None,
        "raw": None,
    }

