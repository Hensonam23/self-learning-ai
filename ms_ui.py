#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel


APP_NAME = "MachineSpirit UI"
VERSION = "0.3.7"

REPO_DIR = Path(__file__).resolve().parent
DATA_DIR = REPO_DIR / "data"
KNOWLEDGE_PATH = DATA_DIR / "local_knowledge.json"

CFG_DIR = Path(os.path.expanduser("~/.config/machinespirit"))
SECRETS_PATH = CFG_DIR / "secrets.env"

API_BASE = os.environ.get("MS_API_BASE", "http://127.0.0.1:8010").rstrip("/")
MS_API_KEY = os.environ.get("MS_API_KEY", "").strip()

app = FastAPI(title=APP_NAME, version=VERSION)

_KNOW_LOCK = threading.Lock()


# -----------------------------
# Helpers
# -----------------------------
def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _load_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    except Exception:
        return {}
    return out


def _ensure_api_key_loaded() -> None:
    global MS_API_KEY
    if MS_API_KEY:
        return
    env = _load_env_file(SECRETS_PATH)
    MS_API_KEY = (env.get("MS_API_KEY", "") or "").strip()


def _api_headers() -> Dict[str, str]:
    _ensure_api_key_loaded()
    if not MS_API_KEY:
        return {"Content-Type": "application/json"}
    return {"Content-Type": "application/json", "X-API-Key": MS_API_KEY}


def _api_get(path: str, timeout: int = 8) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, headers=_api_headers(), timeout=timeout)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise HTTPException(status_code=r.status_code, detail=detail or f"API GET failed: {r.status_code}")
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"API GET error: {type(e).__name__}: {e}")


def _api_post(path: str, payload: Dict[str, Any], timeout: int = 18) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    try:
        r = requests.post(url, headers=_api_headers(), json=payload, timeout=timeout)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise HTTPException(status_code=r.status_code, detail=detail or f"API POST failed: {r.status_code}")
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"API POST error: {type(e).__name__}: {e}")


def _load_knowledge_root():
    """
    Returns (root_obj, mapping, mode)

    mode:
      - "map": root_obj IS the mapping (topic -> entry)
      - "knowledge": root_obj["knowledge"] is the mapping
      - "topics": root_obj["topics"] is the mapping
    """
    if not KNOWLEDGE_PATH.exists():
        return {}, {}, "map"

    try:
        root = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}, "map"

    if isinstance(root, dict):
        if isinstance(root.get("knowledge"), dict):
            return root, root["knowledge"], "knowledge"
        if isinstance(root.get("topics"), dict):
            return root, root["topics"], "topics"
        return root, root, "map"

    return {}, {}, "map"


def _save_knowledge_root(root, mapping, mode: str) -> None:
    if mode == "knowledge":
        out_obj = dict(root or {})
        out_obj["knowledge"] = mapping
    elif mode == "topics":
        out_obj = dict(root or {})
        out_obj["topics"] = mapping
    else:
        out_obj = mapping

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = str(KNOWLEDGE_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out_obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, KNOWLEDGE_PATH)


def overwrite_topic(topic: str, answer: str, confidence: float = 0.95) -> str:
    t = (topic or "").strip()
    a = (answer or "").strip()
    if not t:
        raise HTTPException(status_code=422, detail="override requires topic")
    if not a:
        raise HTTPException(status_code=422, detail="override requires answer")

    root, mp, mode = _load_knowledge_root()
    mp = dict(mp or {})

    # prefer existing key if it exists case-insensitive (prevents duplicates)
    key_l = t.lower()
    chosen_key = None
    if key_l in mp:
        chosen_key = key_l
    else:
        for k in mp.keys():
            if isinstance(k, str) and k.lower() == key_l:
                chosen_key = k
                break
    if not chosen_key:
        chosen_key = key_l

    entry = mp.get(chosen_key)
    if not isinstance(entry, dict):
        entry = {}

    entry["answer"] = a
    entry["confidence"] = float(confidence if confidence is not None else 0.95)
    entry["taught_by_user"] = True
    entry["updated"] = _iso_now()
    entry["sources"] = ["user (ui correction)"]
    entry["notes"] = "Corrected by user in UI (overrode previous answer)."
    entry.pop("evidence", None)

    mp[chosen_key] = entry
    _save_knowledge_root(root, mp, mode)
    return chosen_key


# -----------------------------
# Models
# -----------------------------
class AskIn(BaseModel):
    text: str


class OverrideIn(BaseModel):
    topic: str
    answer: str
    confidence: Optional[float] = 0.95


class ThemeIn(BaseModel):
    theme: str
    intensity: str


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def root():
    return RedirectResponse(url="/ui")


@app.get("/health")
def health():
    return {"ok": True, "service": "machinespirit-ui", "version": VERSION, "api": API_BASE}


@app.get("/ui")
def ui():
    html = HTML_TEMPLATE.replace("__API_BASE__", API_BASE)
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/theme")
def api_theme():
    data = _api_get("/theme")
    return data


@app.post("/api/theme")
def api_theme_set(payload: ThemeIn):
    data = _api_post("/theme", {"theme": payload.theme, "intensity": payload.intensity})
    return data


@app.post("/api/theme/off")
def api_theme_off():
    data = _api_post("/theme", {"theme": "none", "intensity": "light"})
    return data


@app.post("/api/ask")
def api_ask(payload: AskIn):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    data = _api_post("/ask", {"text": text})
    ans = data.get("answer") if isinstance(data, dict) else None
    if not ans:
        return JSONResponse({"answer": "UI error: no answer returned", "raw": data}, status_code=502)
    return {"answer": ans}


@app.post("/api/override")
def api_override(payload: OverrideIn):
    with _KNOW_LOCK:
        t = overwrite_topic(payload.topic, payload.answer, payload.confidence or 0.95)
    return {"ok": True, "answer": f"Got it — I replaced my saved answer for '{t}'."}


# -----------------------------
# HTML template
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

    details summary{
      cursor:pointer;
      user-select:none;
      font-weight:700;
      font-size:14px;
      list-style:none;
      outline:none;
    }
    details summary::-webkit-details-marker{ display:none; }

    .hint{ margin-top:6px; color:var(--muted); font-size:12px; }

    .theme-panel{
      display:grid;
      grid-template-columns: 1fr 220px 200px;
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
          <details id="themeDetails">
            <summary>Theme settings</summary>
            <div class="hint">
              Ask normally. To correct: you can type <b>No, it's actually ...</b> OR just start with <b>NAT is ...</b>
            </div>

            <div class="theme-panel">
              <div class="field">
                <label>Theme name</label>
                <input id="themeName" placeholder="Warhammer 40k" />
              </div>

              <div class="field">
                <label>Intensity</label>
                <select id="themeIntensity">
                  <option value="light">Light</option>
                  <option value="heavy">Heavy</option>
                </select>
              </div>

              <div class="theme-actions">
                <button class="btn" id="applyThemeBtn">Apply</button>
                <button class="btn" id="offThemeBtn">Off</button>
              </div>
            </div>
          </details>
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
  const offThemeBtn = document.getElementById("offThemeBtn");

  const STORAGE_KEY = "ms_chat_v1";

  // conversational correction state
  let lastTopic = "";

  function normalizeTopic(s){
    s = (s || "").trim();
    s = s.replace(/^(what is|what's|define|explain)\s+/i, "").trim();
    return s;
  }

  function extractCorrection(s){
    const t = (s || "").trim();

    // "No, it's actually ...", "Actually, ...", "Correction: ..."
    let m = t.match(/^(no|nah|not quite|correction|actually)[,!\s]+(?:it'?s\s+|it\s+is\s+)?(?:actually\s+)?(.+)$/i);
    if (m && m[2]) return m[2].trim();

    // "That's wrong, it's ..."
    m = t.match(/^that'?s\s+wrong[,!\s]+(?:it'?s\s+|it\s+is\s+)?(.+)$/i);
    if (m && m[1]) return m[1].trim();

    return null;
  }

  function looksLikeAutoCorrection(text){
    // If they just type "NAT is ..." right after asking about NAT,
    // treat it as a correction (no command needed).
    if (!lastTopic) return false;

    const t = (text || "").trim();
    if (!t) return false;
    if (t.startsWith("/")) return false;

    const lt = String(lastTopic || "").trim().toLowerCase();
    const tl = t.toLowerCase();

    // must start with the last topic (or last topic + punctuation)
    if (!(tl.startsWith(lt + " ") || tl.startsWith(lt + ":") || tl.startsWith(lt + "—") || tl.startsWith(lt + "-"))) {
      return false;
    }

    // typical "definition" phrasing
    const defish = (
      tl.startsWith(lt + " is ") ||
      tl.startsWith(lt + " means ") ||
      tl.startsWith(lt + " stands for ") ||
      tl.startsWith(lt + " = ") ||
      tl.startsWith(lt + ":")
    );

    // require some length so we don't trigger on short replies
    if (defish && t.length >= (lt.length + 12)) return true;

    return false;
  }

  function nowHHMM(){
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,"0");
    const mm = String(d.getMinutes()).padStart(2,"0");
    return hh + ":" + mm;
  }

  function linkify(text){
    const s = (text || "");
    const urlRe = /(https?:\/\/[^\s]+)/g;
    return s.replace(urlRe, (m) => `<a href="${m}" target="_blank" rel="noopener noreferrer">${m}</a>`);
  }

  function loadChat(){
    try{
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    }catch(e){
      return [];
    }
  }

  function saveChat(arr){
    try{ localStorage.setItem(STORAGE_KEY, JSON.stringify(arr)); }catch(e){}
  }

  function appendMessage(role, name, content, when){
    const row = document.createElement("div");
    row.className = "row " + (role === "user" ? "user" : "ai");

    if (role !== "user"){
      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "MS";
      row.appendChild(av);
    }

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const meta = document.createElement("div");
    meta.className = "meta";
    const nm = document.createElement("div");
    nm.className = "name";
    nm.textContent = name || (role === "user" ? "You" : "MachineSpirit");
    const tm = document.createElement("div");
    tm.textContent = when || nowHHMM();
    meta.appendChild(nm);
    meta.appendChild(tm);

    const body = document.createElement("div");
    body.className = "content";
    body.innerHTML = linkify(content);

    bubble.appendChild(meta);
    bubble.appendChild(body);

    row.appendChild(bubble);

    if (role === "user"){
      const spacer = document.createElement("div");
      spacer.style.width = "30px";
      row.appendChild(spacer);
    }

    chatEl.appendChild(row);
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function renderAll(){
    chatEl.innerHTML = "";
    const arr = loadChat();
    for (const m of arr){
      appendMessage(m.role, m.name, m.content, m.when);
    }
  }

  function pushAndRender(role, name, content){
    const arr = loadChat();
    const msg = { role, name, content, when: nowHHMM() };
    arr.push(msg);
    saveChat(arr);
    appendMessage(msg.role, msg.name, msg.content, msg.when);
  }

  async function refreshThemePill(){
    try{
      const r = await fetch("/api/theme");
      const out = await r.json().catch(() => ({}));
      if (!r.ok){
        themePill.textContent = "Theme: (api error)";
        return;
      }
      const theme = out.theme || "none";
      const intensity = out.intensity || "light";
      themePill.textContent = `Theme: ${theme} (${intensity})`;
      themeName.value = (theme && theme !== "none") ? theme : "";
      themeIntensity.value = intensity;
    }catch(e){
      themePill.textContent = "Theme: (offline)";
    }
  }

  async function ask(text){
    const corrected = extractCorrection(text);
    const autoCorr = looksLikeAutoCorrection(text);
    const isCorrection = !!((corrected && lastTopic) || autoCorr);

    const endpoint = isCorrection ? "/api/override" : "/api/ask";
    const payload = isCorrection
      ? { topic: lastTopic, answer: (corrected ? corrected : (text || "").trim()) }
      : { text: text };

    // set last topic only when asking (not correcting), and ignore slash commands
    if (!isCorrection && !(text || "").trim().startsWith("/")){
      lastTopic = normalizeTopic(text).toLowerCase();
    }

    const r = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const out = await r.json().catch(() => ({}));
    if (!r.ok) return out.answer || out.detail || "UI error: failed to ask";
    return out.answer || out.text || out.response || JSON.stringify(out);
  }

  async function sendCurrent(){
    const text = (msgEl.value || "").trim();
    if (!text) return;

    msgEl.value = "";
    pushAndRender("user", "You", text);

    const reply = await ask(text);
    pushAndRender("ai", "MachineSpirit", reply);
  }

  sendBtn.addEventListener("click", sendCurrent);
  msgEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey){
      e.preventDefault();
      sendCurrent();
    }
  });

  resetBtn.addEventListener("click", () => {
    try{ localStorage.removeItem(STORAGE_KEY); }catch(e){}
    lastTopic = "";
    renderAll();
  });

  applyThemeBtn.addEventListener("click", async () => {
    const name = (themeName.value || "").trim() || "none";
    const intensity = (themeIntensity.value || "light").trim();
    try{
      const r = await fetch("/api/theme", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: name, intensity: intensity }),
      });
      await r.json().catch(() => ({}));
    }catch(e){}
    refreshThemePill();
  });

  offThemeBtn.addEventListener("click", async () => {
    try{
      const r = await fetch("/api/theme/off", { method: "POST" });
      await r.json().catch(() => ({}));
    }catch(e){}
    refreshThemePill();
  });

  // boot
  renderAll();
  refreshThemePill();
</script>
</body>
</html>
"""
