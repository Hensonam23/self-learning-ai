#!/usr/bin/env python3
import os
import json
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

APP_TITLE = "MachineSpirit UI"

# API that answers questions
API_BASE = os.environ.get("MS_API_URL", "http://127.0.0.1:8010").rstrip("/")
API_ASK_URL = os.environ.get("MS_API_ASK_URL", f"{API_BASE}/ask")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Optional client API key support (for when ms_api requires an inbound key)
API_KEY_FILE = os.path.join(DATA_DIR, "api_key.txt")
API_KEY_HEADER_FILE = os.path.join(DATA_DIR, "api_key_header.txt")
API_KEY_DEFAULT_HEADER = os.environ.get("MS_API_KEY_HEADER", "X-API-Key")

# Theme store fallback (only used if ms_theme module not available)
FALLBACK_THEME_PATH = os.path.join(DATA_DIR, "theme_state.json")

DEFAULT_THEME_STATE = {
    "enabled": True,
    "theme": "Warhammer 40k",
    "intensity": 2,  # 1=light, 2=heavy
}

app = FastAPI(title=APP_TITLE)

# LAN-friendly default; tighten later if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Utilities
# -----------------------------

def _intensity_label(level: int) -> str:
    try:
        return "heavy" if int(level) >= 2 else "light"
    except Exception:
        return "heavy"

def _atomic_write_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _read_secret_line(path: str) -> Optional[str]:
    try:
        s = open(path, "r", encoding="utf-8").read().strip()
        return s if s else None
    except Exception:
        return None

def _get_client_api_key() -> Optional[str]:
    return (
        os.environ.get("MS_API_KEY")
        or os.environ.get("MACHINESPIRIT_API_KEY")
        or _read_secret_line(API_KEY_FILE)
    )

def _get_client_api_key_header() -> str:
    return _read_secret_line(API_KEY_HEADER_FILE) or API_KEY_DEFAULT_HEADER

def _normalize_theme_state(raw: Dict[str, Any]) -> Dict[str, Any]:
    st = dict(DEFAULT_THEME_STATE)

    if "enabled" in raw:
        st["enabled"] = bool(raw.get("enabled"))
    elif "off" in raw:
        st["enabled"] = not bool(raw.get("off"))

    if "theme" in raw and raw.get("theme") is not None:
        name = str(raw.get("theme")).strip()
        if name:
            st["theme"] = name
    elif "name" in raw and raw.get("name") is not None:
        name = str(raw.get("name")).strip()
        if name:
            st["theme"] = name

    lvl = raw.get("intensity", raw.get("level", raw.get("mode", st["intensity"])))
    try:
        lvl_int = int(lvl)
    except Exception:
        lvl_int = st["intensity"]

    if lvl_int < 1:
        lvl_int = 1
    if lvl_int > 2:
        lvl_int = 2
    st["intensity"] = lvl_int
    return st

def _load_theme_state() -> Dict[str, Any]:
    # Prefer ms_theme module if available
    try:
        import ms_theme  # type: ignore

        for fn_name in ["get_theme_state", "load_theme_state", "get_theme", "load_theme"]:
            fn = getattr(ms_theme, fn_name, None)
            if callable(fn):
                st = fn()
                if isinstance(st, dict):
                    return _normalize_theme_state(st)

        for attr in ["THEME_PATH", "THEME_STATE_PATH", "THEME_FILE", "THEME_JSON_PATH"]:
            p = getattr(ms_theme, attr, None)
            if isinstance(p, str) and p:
                st = _read_json(p)
                if isinstance(st, dict):
                    return _normalize_theme_state(st)
    except Exception:
        pass

    st = _read_json(FALLBACK_THEME_PATH)
    if isinstance(st, dict):
        return _normalize_theme_state(st)

    return _normalize_theme_state(DEFAULT_THEME_STATE)

def _save_theme_state(state: Dict[str, Any]) -> None:
    state = _normalize_theme_state(state)

    try:
        import ms_theme  # type: ignore

        for fn_name in ["set_theme_state", "save_theme_state", "set_theme", "save_theme"]:
            fn = getattr(ms_theme, fn_name, None)
            if callable(fn):
                try:
                    fn(state)
                    return
                except TypeError:
                    try:
                        fn(
                            theme=state.get("theme"),
                            intensity=state.get("intensity"),
                            enabled=state.get("enabled"),
                        )
                        return
                    except Exception:
                        pass

        for attr in ["THEME_PATH", "THEME_STATE_PATH", "THEME_FILE", "THEME_JSON_PATH"]:
            p = getattr(ms_theme, attr, None)
            if isinstance(p, str) and p:
                _atomic_write_json(p, state)
                return
    except Exception:
        pass

    _atomic_write_json(FALLBACK_THEME_PATH, state)

def _http_post_json(url: str, payload: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")

    headers = {"Content-Type": "application/json"}

    # If API key exists, attach it (fixes your 401 issue)
    key = _get_client_api_key()
    if key:
        hdr = _get_client_api_key_header().strip()
        if hdr.lower() == "authorization":
            if not key.lower().startswith("bearer "):
                key = "Bearer " + key
        headers[hdr] = key

    req = urllib.request.Request(
        url=url,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                return {"answer": raw.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        return {"error": f"HTTPError {e.code}", "detail": body}
    except Exception as e:
        return {"error": "Request failed", "detail": str(e)}

def _handle_theme_command(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t.startswith("/theme"):
        return None

    parts = t.split()

    if len(parts) == 1:
        st = _load_theme_state()
        if st.get("enabled"):
            return (
                "Theme commands:\n"
                "/theme off\n"
                "/theme set <theme name> 1  (light)\n"
                "/theme set <theme name> 2  (heavy)\n\n"
                f"Current: {st.get('theme')} ({_intensity_label(st.get('intensity', 2))})"
            )
        return (
            "Theme commands:\n"
            "/theme off\n"
            "/theme set <theme name> 1  (light)\n"
            "/theme set <theme name> 2  (heavy)\n\n"
            "Current: OFF"
        )

    if len(parts) == 2 and parts[1].lower() == "off":
        st = _load_theme_state()
        st["enabled"] = False
        _save_theme_state(st)
        return "Theme is now OFF."

    if len(parts) >= 4 and parts[1].lower() == "set":
        try:
            lvl = int(parts[-1])
        except Exception:
            lvl = 2
        name = " ".join(parts[2:-1]).strip()
        if not name:
            return "Missing theme name. Example: /theme set Warhammer 40k 2"

        st = _load_theme_state()
        st["enabled"] = True
        st["theme"] = name
        st["intensity"] = 2 if lvl >= 2 else 1
        _save_theme_state(st)
        return f"Theme set to: {st['theme']} ({_intensity_label(st['intensity'])})"

    return "Unknown theme command. Type /theme to see examples."

# -----------------------------
# HTML template (raw string)
# -----------------------------

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
  <meta http-equiv="Pragma" content="no-cache"/>
  <meta http-equiv="Expires" content="0"/>
  <title>MachineSpirit UI</title>
  <style>
    :root{
      --panel: rgba(18, 20, 30, 0.68);
      --border: rgba(255,255,255,0.08);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.60);
      --bubble-ai: rgba(24, 26, 38, 0.85);
      --bubble-user: rgba(34, 72, 160, 0.65);
      --shadow: 0 10px 30px rgba(0,0,0,0.35);
      --radius: 14px;
      --maxw: 980px;
      --font: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial, sans-serif;
    }

    /* FIX: stop background repeating/tiling */
    html, body { height: 100%; }

    body{
      margin:0;
      font-family:var(--font);
      color:var(--text);

      background-color:#0b0f1a;
      background-image:
        radial-gradient(circle at 20% 10%, rgba(80, 120, 255, 0.22), transparent 45%),
        radial-gradient(circle at 80% 30%, rgba(180, 70, 255, 0.18), transparent 50%),
        radial-gradient(circle at 40% 85%, rgba(0, 180, 255, 0.10), transparent 55%),
        linear-gradient(180deg, rgba(10, 12, 22, 0.92), rgba(10, 12, 22, 0.92));
      background-repeat:no-repeat;
      background-size:cover;
      background-attachment:fixed;
      background-position:center top;

      min-height:100vh;
      overflow:hidden;
    }

    a{ color:rgba(140, 190, 255, 0.92); text-decoration:none; }
    a:hover{ text-decoration:underline; }

    .app{ height:100vh; display:flex; flex-direction:column; }

    .topbar{
      display:flex;
      align-items:center;
      justify-content:space-between;
      padding:14px 18px;
      background:rgba(10, 12, 18, 0.55);
      border-bottom:1px solid var(--border);
      backdrop-filter:blur(10px);
    }

    .brand{ display:flex; flex-direction:column; gap:2px; }
    .brand .title{ font-weight:700; font-size:16px; line-height:18px; }
    .brand .sub{ font-size:12px; color:var(--muted); }

    .actions{ display:flex; gap:10px; align-items:center; }

    .pill{
      font-size:12px;
      padding:6px 10px;
      border-radius:999px;
      background:rgba(255,255,255,0.06);
      border:1px solid var(--border);
      color:var(--text);
      white-space:nowrap;
    }

    .btn{
      font-size:12px;
      padding:7px 10px;
      border-radius:10px;
      border:1px solid var(--border);
      background:rgba(255,255,255,0.06);
      color:var(--text);
      cursor:pointer;
    }
    .btn:hover{ background:rgba(255,255,255,0.10); }

    .wrap{
      flex:1;
      display:flex;
      justify-content:center;
      padding:14px 16px 18px 16px;
      overflow:hidden;
    }

    .card{
      width:min(var(--maxw), 100%);
      height:100%;
      display:flex;
      flex-direction:column;
      background:var(--panel);
      border:1px solid var(--border);
      border-radius:18px;
      box-shadow:var(--shadow);
      overflow:hidden;
      backdrop-filter:blur(12px);
    }

    .panel{
      padding:12px 14px;
      border-bottom:1px solid var(--border);
      background:rgba(10, 12, 18, 0.35);
    }

    .panel h3{ margin:0; font-size:14px; font-weight:700; }
    .panel .hint{ margin-top:6px; color:var(--muted); font-size:12px; }

    .theme-panel{
      display:grid;
      grid-template-columns: 1fr 220px 200px; /* FIX overlap: give buttons real space */
      gap:12px;
      margin-top:10px;
      padding-top:10px;
      border-top:1px dashed rgba(255,255,255,0.12);
      align-items:end;
    }

    .field label{ display:block; font-size:12px; color:var(--muted); margin-bottom:6px; }

    .field input, .field select{
      width:100%;
      padding:10px 10px;
      border-radius:12px;
      border:1px solid var(--border);
      background:rgba(0,0,0,0.25);
      color:var(--text);
      outline:none;
    }
    .field input:focus, .field select:focus{ border-color:rgba(140, 190, 255, 0.45); }

    .theme-actions{
      display:grid;
      grid-template-columns: 1fr 1fr; /* FIX overlap */
      gap:10px;
    }
    .theme-actions .btn{ width:100%; }

    .chat{
      flex:1;
      overflow:auto;
      padding:14px 14px 10px 14px;
    }

    .row{ display:flex; margin:10px 0; gap:10px; align-items:flex-start; }
    .row.user{ justify-content:flex-end; }

    .avatar{
      width:30px; height:30px;
      border-radius:999px;
      display:grid;
      place-items:center;
      background:rgba(255,255,255,0.08);
      border:1px solid var(--border);
      color:var(--text);
      font-weight:800;
      flex:0 0 auto;
    }

    .bubble{
      max-width:74%;
      border-radius:var(--radius);
      padding:12px 12px;
      border:1px solid var(--border);
      background:var(--bubble-ai);
      box-shadow:0 6px 18px rgba(0,0,0,0.25);
    }
    .row.user .bubble{ background:var(--bubble-user); }

    .meta{
      display:flex;
      gap:8px;
      align-items:baseline;
      margin-bottom:6px;
      color:var(--muted);
      font-size:11px;
    }
    .meta .name{ font-weight:700; color:rgba(255,255,255,0.78); }
    .content{ white-space:pre-wrap; line-height:1.35; font-size:13px; }

    .small{ font-size:12px; color:var(--muted); padding:0 14px 12px 14px; }

    .inputbar{
      padding:12px 12px;
      border-top:1px solid var(--border);
      background:rgba(10, 12, 18, 0.45);
      display:grid;
      grid-template-columns: 1fr 96px;
      gap:10px;
    }

    textarea{
      resize:none;
      height:44px;
      padding:10px 10px;
      border-radius:12px;
      border:1px solid var(--border);
      background:rgba(0,0,0,0.20);
      color:var(--text);
      outline:none;
      font-family:var(--font);
      font-size:13px;
      line-height:1.2;
    }
    textarea:focus{ border-color:rgba(140, 190, 255, 0.45); }

    .sendbtn{
      height:44px;
      border-radius:12px;
      border:1px solid var(--border);
      background:rgba(255,255,255,0.08);
      color:var(--text);
      cursor:pointer;
      font-weight:700;
    }
    .sendbtn:hover{ background:rgba(255,255,255,0.12); }

    @media (max-width: 900px){
      .theme-panel{ grid-template-columns: 1fr; }
      .bubble{ max-width:86%; }
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="topbar">
      <div class="brand">
        <div class="title">MachineSpirit UI</div>
        <div class="sub">LAN chat • API: __API_BASE__</div>
      </div>

      <div class="actions">
        <div id="themePill" class="pill">Theme: loading...</div>
        <button class="btn" id="resetBtn">Reset chat</button>
      </div>
    </div>

    <div class="wrap">
      <div class="card">
        <div class="panel">
          <h3>Theme settings</h3>
          <div class="hint">Try: <b>what is nat</b> or type <b>/theme</b> in chat</div>

          <div class="theme-panel">
            <div class="field">
              <label>Theme name</label>
              <input id="themeName" type="text" placeholder="Warhammer 40k" />
            </div>

            <div class="field">
              <label>Intensity</label>
              <select id="themeIntensity">
                <option value="1">Light</option>
                <option value="2">Heavy</option>
              </select>
            </div>

            <div class="theme-actions">
              <button class="btn" id="applyThemeBtn">Apply</button>
              <button class="btn" id="themeOffBtn">Off</button>
            </div>
          </div>
        </div>

        <div id="chat" class="chat"></div>

        <div class="small">Enter = send. Shift+Enter = new line.</div>

        <div class="inputbar">
          <textarea id="msg" placeholder="Type a message..."></textarea>
          <button class="sendbtn" id="sendBtn">Send</button>
        </div>
      </div>
    </div>
  </div>

<script>
  const chatEl = document.getElementById("chat");
  const msgEl = document.getElementById("msg");
  const sendBtn = document.getElementById("sendBtn");
  const resetBtn = document.getElementById("resetBtn");
  const themePill = document.getElementById("themePill");
  const themeName = document.getElementById("themeName");
  const themeIntensity = document.getElementById("themeIntensity");
  const applyThemeBtn = document.getElementById("applyThemeBtn");
  const themeOffBtn = document.getElementById("themeOffBtn");

  const LS_CHAT = "machinespirit_chat_v1";

  function escapeHtml(s) {
    return (s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function linkify(text) {
    const escaped = escapeHtml(text);
    const urlRe = /(https?:\/\/[^\s)]+)|((?:www\.)[^\s)]+)/g;
    return escaped.replace(urlRe, (match) => {
      let href = match;
      if (!href.startsWith("http")) href = "http://" + href;
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${match}</a>`;
    });
  }

  function loadChat() {
    try {
      const raw = localStorage.getItem(LS_CHAT);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) return arr;
    } catch (e) {}
    return [];
  }

  function saveChat(arr) {
    try { localStorage.setItem(LS_CHAT, JSON.stringify(arr)); } catch (e) {}
  }

  function nowHHMM() {
    const d = new Date();
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function appendMessage(role, name, content, when) {
    const row = document.createElement("div");
    row.className = "row " + (role === "user" ? "user" : "ai");

    if (role !== "user") {
      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "MS";
      row.appendChild(av);
    }

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `<span class="name">${escapeHtml(name)}</span><span>${escapeHtml(when)}</span>`;
    bubble.appendChild(meta);

    const body = document.createElement("div");
    body.className = "content";
    body.innerHTML = linkify(content);
    bubble.appendChild(body);

    row.appendChild(bubble);

    if (role === "user") {
      const spacer = document.createElement("div");
      spacer.style.width = "30px";
      row.appendChild(spacer);
    }

    chatEl.appendChild(row);
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function renderAll() {
    chatEl.innerHTML = "";
    const arr = loadChat();
    for (const m of arr) appendMessage(m.role, m.name, m.content, m.when);
  }

  function pushAndRender(role, name, content) {
    const arr = loadChat();
    const msg = { role, name, content, when: nowHHMM() };
    arr.push(msg);
    saveChat(arr);
    appendMessage(role, name, content, msg.when);
  }

  async function ask(text) {
    const r = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) return out.answer || out.detail || "UI error: failed to ask";
    return out.answer || out.text || out.response || JSON.stringify(out);
  }

  async function sendCurrent() {
    const text = (msgEl.value || "").trim();
    if (!text) return;

    msgEl.value = "";
    pushAndRender("user", "You", text);

    // typing indicator
    pushAndRender("ai", "MachineSpirit", "…");

    const arr = loadChat();
    try {
      const answer = await ask(text);
      arr[arr.length - 1] = { role: "ai", name: "MachineSpirit", content: answer, when: nowHHMM() };
      saveChat(arr);
      renderAll();
    } catch (e) {
      arr[arr.length - 1] = { role: "ai", name: "MachineSpirit", content: "UI error: " + e.message, when: nowHHMM() };
      saveChat(arr);
      renderAll();
    }
  }

  async function getTheme() {
    const r = await fetch("/api/theme");
    if (!r.ok) throw new Error("theme fetch failed");
    return await r.json();
  }

  function setThemePill(st) {
    if (!st || !st.enabled) {
      themePill.textContent = "Theme: off";
      return;
    }
    const label = st.intensity_label || (st.intensity >= 2 ? "heavy" : "light");
    themePill.textContent = "Theme: " + st.theme + " (" + label + ")";
  }

  async function refreshThemeUI() {
    try {
      const st = await getTheme();
      setThemePill(st);
      themeName.value = st.theme || "";
      themeIntensity.value = String(st.intensity || 2);
    } catch (e) {
      themePill.textContent = "Theme: error";
    }
  }

  async function postTheme(payload) {
    const r = await fetch("/api/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(out.detail || "theme set failed");
    return out;
  }

  sendBtn.addEventListener("click", () => sendCurrent());

  msgEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      sendCurrent();
    }
  });

  resetBtn.addEventListener("click", () => {
    localStorage.removeItem(LS_CHAT);
    renderAll();
  });

  applyThemeBtn.addEventListener("click", async () => {
    const name = (themeName.value || "").trim();
    const lvl = parseInt(themeIntensity.value || "2", 10);
    if (!name) {
      pushAndRender("ai", "MachineSpirit", "Missing theme name. Example: Warhammer 40k");
      return;
    }
    try {
      const st = await postTheme({ enabled: true, theme: name, intensity: lvl });
      await refreshThemeUI();
      pushAndRender("ai", "MachineSpirit", "Theme set to: " + st.theme + " (" + st.intensity_label + ")");
    } catch (e) {
      pushAndRender("ai", "MachineSpirit", "Theme error: " + e.message);
    }
  });

  themeOffBtn.addEventListener("click", async () => {
    try {
      await postTheme({ enabled: false });
      await refreshThemeUI();
      pushAndRender("ai", "MachineSpirit", "Theme is now OFF.");
    } catch (e) {
      pushAndRender("ai", "MachineSpirit", "Theme error: " + e.message);
    }
  });

  (async function init() {
    renderAll();
    await refreshThemeUI();
    msgEl.focus();
  })();
</script>
</body>
</html>
"""

# -----------------------------
# Routes
# -----------------------------

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui")

@app.get("/health")
def health():
    return {"ok": True, "service": "machinespirit-ui"}

@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    html = HTML_TEMPLATE.replace("__API_BASE__", API_BASE)
    return HTMLResponse(content=html)

@app.get("/api/theme")
def api_get_theme():
    st = _load_theme_state()
    out = {
        "enabled": bool(st.get("enabled", True)),
        "theme": str(st.get("theme", "")),
        "intensity": int(st.get("intensity", 2)),
    }
    out["intensity_label"] = _intensity_label(out["intensity"])
    return out

@app.post("/api/theme")
async def api_set_theme(req: Request):
    try:
        payload = await req.json()
    except Exception:
        payload = {}

    st = _load_theme_state()

    if "enabled" in payload:
        st["enabled"] = bool(payload.get("enabled"))
    if "theme" in payload and payload.get("theme") is not None:
        name = str(payload.get("theme")).strip()
        if name:
            st["theme"] = name
            st["enabled"] = True
    if "intensity" in payload and payload.get("intensity") is not None:
        try:
            lvl = int(payload.get("intensity"))
        except Exception:
            lvl = int(st.get("intensity", 2))
        st["intensity"] = 2 if lvl >= 2 else 1
        st["enabled"] = True

    _save_theme_state(st)

    out = {
        "enabled": bool(st.get("enabled", True)),
        "theme": str(st.get("theme", "")),
        "intensity": int(st.get("intensity", 2)),
    }
    out["intensity_label"] = _intensity_label(out["intensity"])
    return out

@app.post("/api/ask")
async def api_ask(req: Request):
    try:
        payload = await req.json()
    except Exception:
        payload = {}

    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"answer": "Missing text"})

    # Local /theme handling so it always works even if API changes
    theme_msg = _handle_theme_command(text)
    if theme_msg is not None:
        return {"answer": theme_msg}

    api_resp = _http_post_json(API_ASK_URL, {"text": text}, timeout=25)

    if isinstance(api_resp, dict):
        for key in ["answer", "text", "response", "output", "result"]:
            if key in api_resp and isinstance(api_resp[key], str) and api_resp[key].strip():
                return {"answer": api_resp[key]}

        if "error" in api_resp:
            msg = f"API error: {api_resp.get('error')}\n{api_resp.get('detail','')}".strip()
            return JSONResponse(status_code=502, content={"answer": msg})

        return {"answer": json.dumps(api_resp, indent=2, ensure_ascii=False)}

    return {"answer": str(api_resp)}

@app.get("/ui/ask", include_in_schema=False)
def ui_ask_get_redirect():
    return RedirectResponse(url="/ui")
