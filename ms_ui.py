#!/usr/bin/env python3
from __future__ import annotations

import datetime as _dt
import os
import json
import re
from pathlib import Path
from typing import Any, Dict

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

APP_NAME = "MachineSpirit UI"
VERSION = "0.3.11"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()

API_BASE = (os.environ.get("MS_API_BASE", "http://127.0.0.1:8010") or "http://127.0.0.1:8010").rstrip("/")
PUBLIC_API_BASE = (os.environ.get("MS_PUBLIC_API_BASE", "") or "").rstrip("/")
PUBLIC_API_DISPLAY = (os.environ.get('MS_PUBLIC_API_DISPLAY', '/api') or '/api')
PUBLIC_API_DISPLAY = (PUBLIC_API_BASE or "/api")
SECRETS_FILE = Path(os.path.expanduser("~/.config/machinespirit/secrets.env"))

app = FastAPI(title=APP_NAME, version=VERSION)


def _iso_now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _load_api_key() -> str:
    k = (os.environ.get("MS_API_KEY", "") or "").strip()
    if k:
        return k

    if SECRETS_FILE.exists():
        try:
            txt = SECRETS_FILE.read_text(encoding="utf-8", errors="replace")
            for line in txt.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("MS_API_KEY="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass

    return ""


def _api_headers() -> Dict[str, str]:
    key = _load_api_key()
    if not key:
        raise HTTPException(status_code=500, detail="MS_API_KEY is not set for the UI service")
    return {"Content-Type": "application/json", "X-API-Key": key}


def _normalize_topic(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"^\s*(what is|what's|what are|define|explain)\s+", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"[?!.]+$", "", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ----------------------------
# Models
# ----------------------------
class AskIn(BaseModel):
    text: str


class OverrideIn(BaseModel):
    topic: str
    answer: str


class ThemeIn(BaseModel):
    theme: str
    intensity: str


# ----------------------------
# Routes
# ----------------------------
@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "service": "machinespirit-ui", "version": VERSION, "api": API_BASE}


@app.get("/ui")
def ui() -> HTMLResponse:
    return HTMLResponse(HTML_TEMPLATE.replace("__API_BASE__", API_BASE))


@app.get("/api/theme")
def api_theme() -> JSONResponse:
    try:
        r = requests.get(f"{API_BASE}/theme", headers=_api_headers(), timeout=10)
        return JSONResponse(status_code=r.status_code, content=r.json() if r.content else {"ok": False})
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": f"theme proxy error: {type(e).__name__}: {e}"})


@app.post("/api/theme")
def api_theme_set(payload: ThemeIn) -> JSONResponse:
    try:
        r = requests.post(f"{API_BASE}/theme", headers=_api_headers(), json=payload.model_dump(), timeout=10)
        return JSONResponse(status_code=r.status_code, content=r.json() if r.content else {"ok": False})
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": f"theme proxy error: {type(e).__name__}: {e}"})


@app.post("/api/ask")
def api_ask(payload: AskIn) -> JSONResponse:
    try:
        r = requests.post(f"{API_BASE}/ask", headers=_api_headers(), json=payload.model_dump(), timeout=35)
        return JSONResponse(status_code=r.status_code, content=r.json() if r.content else {"ok": False})
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "detail": f"ask proxy error: {type(e).__name__}: {e}"})


@app.post("/api/override")
def api_override(payload: OverrideIn):
    """
    Save a corrected answer into local_knowledge.json (atomic write).
    Never crashes; always returns JSON.
    """
    import datetime as _dt
    import json as _json
    import os as _os
    import re as _re
    from pathlib import Path as _Path

    topic = (payload.topic or "").strip()
    answer = (payload.answer or "").strip()

    if not topic:
        return JSONResponse({"ok": False, "detail": "topic is required"}, status_code=422)
    if not answer:
        return JSONResponse({"ok": False, "detail": "answer is required"}, status_code=422)

    def norm(s: str) -> str:
        t = (s or "").strip()
        t = _re.sub(r"^\s*(what is|what's|define|explain)\s+", "", t, flags=_re.IGNORECASE).strip()
        t = _re.sub(r"[?!.]+$", "", t).strip()
        return t.lower()

    topic_n = norm(topic)
    if not topic_n:
        return JSONResponse({"ok": False, "detail": "topic normalized to empty"}, status_code=400)

    knowledge_path = _Path(_os.environ.get(
        "MS_KNOWLEDGE_PATH",
        str(REPO_DIR / "data" / "local_knowledge.json")
    )).resolve()

    # read db
    try:
        if knowledge_path.exists():
            raw = _json.loads(knowledge_path.read_text(encoding="utf-8", errors="replace") or "{}")
            db = raw if isinstance(raw, dict) else {}
        else:
            db = {}
    except Exception as e:
        db = {}

    ent = db.get(topic_n)
    if not isinstance(ent, dict):
        ent = {}

    ent["answer"] = answer
    ent["taught_by_user"] = True
    ent["notes"] = "override via UI (/api/override)"
    ent["updated"] = _dt.datetime.now().isoformat(timespec="seconds")

    try:
        old_c = float(ent.get("confidence", 0.0) or 0.0)
    except Exception:
        old_c = 0.0
    ent["confidence"] = max(old_c, 0.95)

    if not isinstance(ent.get("sources"), list):
        ent["sources"] = []

    db[topic_n] = ent

    # atomic write
    try:
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = knowledge_path.with_suffix(knowledge_path.suffix + ".tmp")
        tmp.write_text(_json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _os.replace(tmp, knowledge_path)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        if len(tb) > 4000:
            tb = tb[-4000:]
        return JSONResponse({"ok": False, "detail": f"write failed: {type(e).__name__}: {e}", "trace": tb}, status_code=500)

    return {"ok": True, "topic": topic_n}


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
    
/* --- MachineSpirit Admin UI (floating) --- */
#msAdminState{ display:none; }

/* --- end MachineSpirit Admin UI --- */
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

    html, body { height:100%; }

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
      cursor:pointer;
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
      display:none;
      margin-top:10px;
      padding-top:10px;
      border-top:1px dashed rgba(255,255,255,0.12);
    }

    .theme-grid{
      display:grid;
      grid-template-columns: 1fr 200px 220px;
      gap:12px;
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

    .theme-actions{
      display:grid;
      grid-template-columns: 1fr 1fr;
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

    .sendbtn{
      height:44px;
      border-radius:12px;
      border:1px solid var(--border);
      background:rgba(255,255,255,0.08);
      color:var(--text);
      cursor:pointer;
      font-weight:700;
    }

    @media (max-width: 900px){
      .theme-grid{ grid-template-columns: 1fr; }
      .bubble{ max-width:86%; }
    }
  </style>
</head>
<body>



  <div class="app">
    <div class="topbar">
      <div class="brand">
        <div class="title">MachineSpirit UI</div>
        <div class="sub">LAN chat • API: {PUBLIC_API_DISPLAY}</div>
      </div>

      <div class="actions">
<button class="btn" id="msAdminBtn" type="button">Admin Login</button>
<button class="btn" id="msAdminOutBtn" type="button" style="display:none;">Logout</button>
<span id="msAdminState" style="display:none;">User</span>
<div id="themePill" class="pill">Theme: loading...</div>
        <button class="btn" id="resetBtn">Reset chat</button>
      </div>
    </div>

    <div class="wrap">
      <div class="card">
        <div class="panel">
          <h3>Chat</h3>
          <div class="hint">
            Ask normally. To correct: <b>no it's: ...</b><br/>
            To save your name: <b>my name is &lt;your name&gt;</b>
          </div>

          <div id="themePanel" class="theme-panel">
            <div class="theme-grid">
              <div class="field">
                <label>Theme name</label>
                <input id="themeName" placeholder="Warhammer 40k"/>
              </div>
              <div class="field">
                <label>Intensity</label>
                <select id="themeIntensity">
                  <option value="light">light</option>
                  <option value="heavy">heavy</option>
                </select>
              </div>
              <div class="theme-actions">
                <button class="btn" id="applyThemeBtn">Apply</button>
                <button class="btn" id="offThemeBtn">Off</button>
              </div>
            </div>
            <div style="margin-top:10px; text-align:right;">
              <button class="btn" id="themeCloseBtn">Close</button>
            </div>
          </div>
        </div>

        <div id="chat" class="chat"></div>

        <div class="inputbar">
          <textarea id="msg" placeholder="Type here..."></textarea>
          <button class="sendbtn" id="sendBtn">Send</button>
        </div>
      </div>
    </div>
  </div>

<script>

// --- MachineSpirit Admin Auth (UI-only, sessionStorage) ---
(function(){
  let msAdminAuth = sessionStorage.getItem("ms_admin_auth") || "";

  function setAdminUI(on){
    const btn = document.getElementById("msAdminBtn");
    const out = document.getElementById("msAdminOutBtn");
    const st  = document.getElementById("msAdminState");
    if(!btn || !out || !st) return;
    if(on){
      btn.style.display = "none";
      out.style.display = "inline-block";
      st.textContent = "Admin";
    } else {
      btn.style.display = "inline-block";
      out.style.display = "none";
      st.textContent = "User";
    }
  }

  async function testAdminAuth(authHeader){
    // Safe test: send invalid override payload.
    // - Without auth: Caddy returns 401
    // - With auth: request reaches UI and returns 422 (topic/answer required)
    try{
      const r = await fetch("/api/override", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": authHeader
        },
        body: JSON.stringify({topic:"", answer:""})
      });
      return (r.status !== 401);
    }catch(e){
      return false;
    }
  }

  async function doLogin(){
    const user = prompt("Admin username:", "admin");
    if(user === null) return;
    const pw = prompt("Admin password:");
    if(pw === null) return;

    const auth = "Basic " + btoa(user + ":" + pw);

    const ok = await testAdminAuth(auth);
    if(!ok){
      alert("Admin login failed (wrong creds or Caddy auth not matching).");
      return;
    }

    msAdminAuth = auth;
    sessionStorage.setItem("ms_admin_auth", msAdminAuth);
    setAdminUI(true);
    alert("Admin unlocked for this tab.");
  }

  function doLogout(){
    msAdminAuth = "";
    sessionStorage.removeItem("ms_admin_auth");
    setAdminUI(false);
  }

  // Wrap fetch to attach Authorization only for admin routes
  const _fetch = window.fetch.bind(window);
  window.fetch = function(input, init){
    init = init || {};
    const url = (typeof input === "string") ? input : (input && input.url) ? input.url : "";
    const method = (init.method || "GET").toUpperCase();

    const needsAdmin =
      url.startsWith("/api/override") ||
      (url.startsWith("/api/theme") && method === "POST");

    if(needsAdmin && msAdminAuth){
      const h = new Headers(init.headers || {});
      h.set("Authorization", msAdminAuth);
      init.headers = h;
    }

    return _fetch(input, init);
  };

  window.addEventListener("DOMContentLoaded", function(){
    setAdminUI(!!msAdminAuth);

    const btn = document.getElementById("msAdminBtn");
    const out = document.getElementById("msAdminOutBtn");
    if(btn) btn.addEventListener("click", doLogin);
    if(out) out.addEventListener("click", doLogout);
  });
})();
// --- end MachineSpirit Admin Auth ---


  const chatEl = document.getElementById("chat");
  const msgEl = document.getElementById("msg");
  const sendBtn = document.getElementById("sendBtn");
  const resetBtn = document.getElementById("resetBtn");
  const themePill = document.getElementById("themePill");
  const themePanel = document.getElementById("themePanel");
  const themeCloseBtn = document.getElementById("themeCloseBtn");
  const applyThemeBtn = document.getElementById("applyThemeBtn");
  const offThemeBtn = document.getElementById("offThemeBtn");
  const themeNameEl = document.getElementById("themeName");
  const themeIntensityEl = document.getElementById("themeIntensity");

  const STORE_KEY = "machinespirit.chat.v1";

  let lastQuestion = "";
  let lastTopic = "";

  function nowHHMM(){
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  }

  function loadChat(){
    try{
      const raw = localStorage.getItem(STORE_KEY);
      if(!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    }catch(e){
      return [];
    }
  }

  function saveChat(arr){
    try{
      localStorage.setItem(STORE_KEY, JSON.stringify(arr));
    }catch(e){}
  }

  function appendMessage(role, name, content, when){
    const row = document.createElement("div");
    row.className = "row " + (role === "user" ? "user" : "ai");

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "Y" : "MS";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const meta = document.createElement("div");
    meta.className = "meta";

    const nm = document.createElement("div");
    nm.className = "name";
    nm.textContent = name;

    const tm = document.createElement("div");
    tm.textContent = when || "";

    meta.appendChild(nm);
    meta.appendChild(tm);

    const body = document.createElement("div");
    body.className = "content";
    body.textContent = content;

    bubble.appendChild(meta);
    bubble.appendChild(body);

    if(role === "user"){
      row.appendChild(bubble);
      row.appendChild(avatar);
    }else{
      row.appendChild(avatar);
      row.appendChild(bubble);
    }

    chatEl.appendChild(row);
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function renderAll(){
    chatEl.innerHTML = "";
    const arr = loadChat();
    for(const m of arr){
      appendMessage(m.role, m.name, m.content, m.when);
    }
  }

  function pushAndRender(role, name, content){
    const arr = loadChat();
    const msg = { role, name, content, when: nowHHMM() };
    arr.push(msg);
    saveChat(arr);
    appendMessage(role, name, content, msg.when);
  }

  function normalizeTopic(s){
    let t = (s || "").trim();
    t = t.replace(/^\s*(what is|what's|what are|define|explain)\s+/i, "").trim();
    t = t.replace(/[?!.]+$/g, "").trim();
    t = t.replace(/\s+/g, " ").trim();
    return t;
  }

  function extractCorrection(s){
    const t = (s || "").trim();
    let m = t.match(/^(?:no[, ]+)?(?:nah[, ]+)?(?:not quite[, ]+)?(?:correction[:, ]+)?(?:it'?s\s+)?actually[:\s]+(.+)$/i);
    if(m && m[1]) return m[1].trim();

    m = t.match(/^(?:no[, ]+)?(?:it'?s\s+)?(?:actually\s+)?[:\s]*(.+)$/i);
    // NOTE: we don't want to treat every "no ..." as correction, so require "no it's:" forms below.
    // Keeping this minimal.
    return "";
  }

  function extractCorrectionStrict(s){
    const t = (s || "").trim();

    // strongest forms
    let m = t.match(/^(?:no[, ]*)?(?:nope[, ]*)?(?:nah[, ]*)?(?:not quite[, ]*)?(?:that'?s wrong[, ]*)?(?:no\s+it\s+is|no\s+it's|no\s+its|no\s+it’s|no\s+it\s+is:|no\s+it's:|no\s+its:|no\s+it’s:|no\s+it\s+is\s*:|no\s+it'?s\s*:|no\s+its\s*:)\s*(.+)$/i);
    if(m && m[1]) return m[1].trim();

    // "correction: ..."
    m = t.match(/^correction[:\s]+(.+)$/i);
    if(m && m[1]) return m[1].trim();

    return "";
  }

  function extractNameTeach(s){
    const t = (s || "").trim();
    let m = t.match(/^my name is\s+(.+)$/i);
    if(m && m[1]) return m[1].trim();

    m = t.match(/^i am\s+(.+)$/i);
    if(m && m[1]) return m[1].trim();

    return "";
  }

  function setThemePanel(open){
    themePanel.style.display = open ? "block" : "none";
  }

  async function refreshTheme(){
    try{
      const r = await fetch("/api/theme");
      const out = await r.json().catch(() => ({}));
      if(!out || out.ok === false){
        themePill.textContent = "Theme: (api error)";
        return;
      }
      const label = `${out.theme || "none"} (${out.intensity || "light"})`;
      themePill.textContent = "Theme: " + label;

      themeNameEl.value = out.theme && out.theme !== "none" ? out.theme : "Warhammer 40k";
      themeIntensityEl.value = (out.intensity === "heavy") ? "heavy" : "light";
    }catch(e){
      themePill.textContent = "Theme: (api error)";
    }
  }

  async function applyTheme(){
    const theme = (themeNameEl.value || "").trim() || "Warhammer 40k";
    const intensity = themeIntensityEl.value || "light";
    await fetch("/api/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme, intensity })
    }).then(r => r.json()).catch(() => ({}));
    await refreshTheme();
    setThemePanel(false);
  }

  async function offTheme(){
    await fetch("/api/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme: "none", intensity: "light" })
    }).then(r => r.json()).catch(() => ({}));
    await refreshTheme();
    setThemePanel(false);
  }

  async function ask(text){
    const r = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });
    return await r.json().catch(() => ({}));
  }

  async function override(topic, answer){
    const r = await fetch("/api/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, answer })
    });
    return await r.json().catch(() => ({}));
  }

  async function sendCurrent(){
    const text = (msgEl.value || "").trim();
    if(!text) return;

    msgEl.value = "";
    pushAndRender("user", "You", text);

    // always close theme panel after a send (prevents "stuck open")
    setThemePanel(false);

    // natural name teaching
    const nameTeach = extractNameTeach(text);
    if(nameTeach){
      const res = await override("my name", nameTeach);
      if(res && res.ok){
        lastTopic = "my name";
        pushAndRender("ai", "MachineSpirit", `Got it — your name is saved as "${nameTeach}".`);
        return;
      }else{
        pushAndRender("ai", "MachineSpirit", `Name save failed: ${res.detail || "unknown error"}`);
        return;
      }
    }

    // corrections (strict)
    const correction = extractCorrectionStrict(text);
    if(correction){
      if(!lastTopic){
        pushAndRender("ai", "MachineSpirit", "I don’t know what to replace yet. Ask a question first, then correct it.");
        return;
      }
      const res = await override(lastTopic, correction);
      if(res && res.ok){
        pushAndRender("ai", "MachineSpirit", `Got it — I replaced my saved answer for "${res.topic}".`);
      }else{
        pushAndRender("ai", "MachineSpirit", `Override failed: ${res.detail || "unknown error"}`);
      }
      return;
    }

    // normal ask
    lastQuestion = normalizeTopic(text);

    const out = await ask(text);
    if(!out || out.ok === false){
      pushAndRender("ai", "MachineSpirit", out.detail || "API error: failed to ask");
      return;
    }

    const ans = out.answer || out.text || out.response || JSON.stringify(out);
    pushAndRender("ai", "MachineSpirit", ans);

    // IMPORTANT FIX: trust API topic, never parse topic from the answer text
    const apiTopic = normalizeTopic(out.topic || "");
    lastTopic = apiTopic || lastQuestion;

    refreshTheme();
  }

  sendBtn.addEventListener("click", sendCurrent);
  msgEl.addEventListener("keydown", (e) => {
    if(e.key === "Enter" && !e.shiftKey){
      e.preventDefault();
      sendCurrent();
    }
  });

  resetBtn.addEventListener("click", () => {
    localStorage.removeItem(STORE_KEY);
    chatEl.innerHTML = "";
    lastTopic = "";
    lastQuestion = "";
    refreshTheme();
    setThemePanel(false);
  });

  themePill.addEventListener("click", () => setThemePanel(themePanel.style.display === "none"));
  themeCloseBtn.addEventListener("click", () => setThemePanel(false));

  applyThemeBtn.addEventListener("click", applyTheme);
  offThemeBtn.addEventListener("click", offTheme);

  renderAll();
  refreshTheme();
  setThemePanel(false);
</script>
</body>
</html>
"""
