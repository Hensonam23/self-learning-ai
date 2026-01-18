#!/usr/bin/env python3
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Tuple

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

APP_NAME = "MachineSpirit UI"
VERSION = "0.3.9"

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("MS_REPO_DIR", str(BASE_DIR))).resolve()

API_BASE = (os.environ.get("MS_API_BASE", "http://127.0.0.1:8010") or "http://127.0.0.1:8010").rstrip("/")
SECRETS_FILE = Path(os.path.expanduser("~/.config/machinespirit/secrets.env"))

KNOWLEDGE_PATH = Path(
    os.environ.get("MS_KNOWLEDGE_PATH", str(REPO_DIR / "data" / "local_knowledge.json"))
).resolve()

app = FastAPI(title=APP_NAME, version=VERSION)


# ----------------------------
# Helpers
# ----------------------------
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
    t = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"[?!.]+$", "", t).strip()
    m = re.match(r'^\s*\{\s*"text"\s*:\s*"(.+)"\s*\}\s*$', t)
    if m:
        t = m.group(1).strip()
        t = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"[?!.]+$", "", t).strip()
    return t


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _ensure_dict_db(db: Any) -> Dict[str, Any]:
    return db if isinstance(db, dict) else {}


def _override_knowledge(topic: str, new_answer: str) -> Tuple[bool, str]:
    topic_n = _normalize_topic(topic).lower()
    ans = (new_answer or "").strip()
    if not topic_n:
        return False, "No topic to override."
    if not ans:
        return False, "Missing corrected answer."

    db_raw = _read_json(KNOWLEDGE_PATH, {})
    db = _ensure_dict_db(db_raw)

    entry = db.get(topic_n)
    if not isinstance(entry, dict):
        entry = {}

    entry["answer"] = ans
    entry["taught_by_user"] = True
    entry["notes"] = "override via UI conversation"
    entry["updated"] = _iso_now()

    try:
        old_c = float(entry.get("confidence", 0.0) or 0.0)
    except Exception:
        old_c = 0.0
    entry["confidence"] = max(old_c, 0.90)

    if "sources" not in entry or not isinstance(entry.get("sources"), list):
        entry["sources"] = entry.get("sources") if isinstance(entry.get("sources"), list) else []

    db[topic_n] = entry
    _write_json_atomic(KNOWLEDGE_PATH, db)
    return True, topic_n


# ----------------------------
# Models
# ----------------------------
class AskIn(BaseModel):
    text: str


class ThemeIn(BaseModel):
    theme: str
    intensity: str


class OverrideIn(BaseModel):
    topic: str
    answer: str


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def root():
    return RedirectResponse(url="/ui")


@app.get("/health")
def health():
    return {"ok": True, "service": "machinespirit-ui", "version": VERSION, "api": API_BASE}


@app.get("/ui", response_class=HTMLResponse)
def ui():
    html = HTML_TEMPLATE.replace("__API_BASE__", API_BASE)
    return HTMLResponse(content=html)


@app.get("/api/theme")
def api_theme_get():
    try:
        r = requests.get(f"{API_BASE}/theme", headers=_api_headers(), timeout=10)
        if r.status_code != 200:
            return JSONResponse(status_code=200, content={"ok": False, "detail": f"API error: {r.status_code} {r.text}"})
        return r.json()
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


@app.post("/api/theme")
def api_theme_set(payload: ThemeIn):
    try:
        r = requests.post(f"{API_BASE}/theme", headers=_api_headers(), json=payload.model_dump(), timeout=10)
        if r.status_code != 200:
            return JSONResponse(status_code=200, content={"ok": False, "detail": f"API error: {r.status_code} {r.text}"})
        return r.json()
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


@app.post("/api/ask")
async def api_ask(payload: dict):
    """
    UI backend ask handler.

    IMPORTANT behavior:
    - Detect conversational corrections like:
        "No, it's actually: ..."
        "no its: ..."
        "no it is: ..."
      (works with smart quotes too)
    - Apply correction to the LAST asked topic and store it (without sending the long correction into brain.py).
    - Normal asks are forwarded to the API as usual.
    """
    import os, json, time, datetime, asyncio
    import requests

    def iso_now() -> str:
        return datetime.datetime.now().isoformat(timespec="seconds")

    def norm_spaces(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    def normalize_topic(s: str) -> str:
        s = norm_spaces(s)
        s = re.sub(r"^\s*(what is|what's|define|explain)\s+", "", s, flags=re.I).strip()
        return s

    def unsmart(s: str) -> str:
        # normalize smart quotes + common mojibake
        s = (s or "")
        s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
        s = s.replace("â��", "'").replace("â��", "'").replace("â��", '"').replace("â��", '"')
        return s

    def extract_correction(text: str):
        t = unsmart(text).strip()

        # accepts:
        # no its: ...
        # no it's: ...
        # no it is: ...
        # no, it's actually: ...
        # correction: ...
        # actually: ...
        pat = re.compile(
            r"^\s*(?:no|nah|nope|actually|correction|correct|fix)\s*(?:,|\s)*"
            r"(?:(?:it\s*(?:is|'?s))|its|that\s*(?:is|'?s)|the\s*correct\s*(?:answer\s*)?(?:is|'?s)|answer\s*(?:is|'?s))?"
            r"\s*[:\-–—]\s*(.+)$",
            re.I | re.S
        )
        m = pat.match(t)
        if not m:
            return None
        return m.group(1).strip()

    # ---- read text
    if not isinstance(payload, dict):
        payload = {}
    text_in = payload.get("text") or ""
    text_in = str(text_in)

    # ---- shared state (last asked topic)
    st = globals().setdefault("_UI_STATE", {"last_topic": "", "last_user": ""})

    # ---- correction flow
    corr = extract_correction(text_in)
    if corr:
        topic = st.get("last_topic") or ""
        topic = normalize_topic(topic)
        topic_key = topic.lower().strip()

        if not topic_key:
            return {"ok": True, "answer": "I can save corrections, but I need you to ask a question first."}

        # If the correction starts with the topic name, strip it out to keep the stored answer clean.
        corr2 = corr
        first_line = corr2.splitlines()[0].strip() if corr2.strip() else ""
        if first_line and len(first_line) <= 80:
            # common case: "BGP route selection\n\nDefinition: ..."
            if first_line.lower() == topic_key:
                corr2 = "\n".join(corr2.splitlines()[1:]).lstrip()

        corr2 = corr2.strip()
        if not corr2:
            return {"ok": True, "answer": f"Correction detected for '{topic}', but the corrected text was empty."}

        # Write to brain knowledge file directly (replace previous)
        repo_dir = Path(__file__).resolve().parent
        data_dir = repo_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        kpath = data_dir / "local_knowledge.json"

        data = {}
        if kpath.exists():
            try:
                data = json.loads(kpath.read_text(encoding="utf-8", errors="replace") or "{}")
            except Exception:
                # salvage: rename bad file so we don't brick the system
                bad = data_dir / f"local_knowledge.bad.{int(time.time())}.json"
                kpath.rename(bad)
                data = {}

        if not isinstance(data, dict):
            data = {}

        entry = data.get(topic_key) if isinstance(data.get(topic_key), dict) else {}
        entry["answer"] = corr2
        entry["confidence"] = float(max(float(entry.get("confidence") or 0.0), 0.95))
        entry["taught_by_user"] = True
        entry["updated"] = iso_now()
        if "sources" not in entry:
            entry["sources"] = []

        data[topic_key] = entry

        tmp = kpath.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(kpath)

        return {"ok": True, "answer": f"Saved correction for: {topic}"}

    # ---- normal ask flow
    # record last asked topic so corrections apply to the right thing
    st["last_user"] = text_in
    st["last_topic"] = normalize_topic(text_in)

    api_base = os.environ.get("MS_API_BASE", "http://127.0.0.1:8010").rstrip("/")
    api_key = os.environ.get("MS_API_KEY", "")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    def do_req():
        return requests.post(
            f"{api_base}/ask",
            headers=headers,
            json={"text": text_in},
            timeout=30
        )

    try:
        r = await asyncio.to_thread(do_req)
        if r.status_code != 200:
            return {"ok": False, "answer": f"API error ({r.status_code}): {r.text[:2000]}"}
        j = r.json()
        ans = (j.get("answer") or "").strip()
        if not ans:
            ans = "No answer returned."
        return {"ok": True, "answer": ans}
    except Exception as e:
        return {"ok": False, "answer": f"UI backend error: {type(e).__name__}: {e}"}

@app.post("/api/override")
def api_override(payload: OverrideIn):
    ok, msg = _override_knowledge(payload.topic, payload.answer)
    if not ok:
        return {"ok": False, "detail": msg}
    return {"ok": True, "topic": msg}


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

    .panelhead{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:10px;
    }

    .panel h3{ margin:0; font-size:14px; font-weight:700; }
    .panel .hint{ margin-top:6px; color:var(--muted); font-size:12px; }

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
        <button class="btn" id="themeToggleBtn">Theme</button>
        <button class="btn" id="resetBtn">Reset chat</button>
      </div>
    </div>

    <div class="wrap">
      <div class="card">

        <!-- Collapsed by default -->
        <div class="panel" id="themePanel" style="display:none;">
          <div class="panelhead">
            <div>
              <h3>Theme settings</h3>
              <div class="hint">
                Ask normally (example: <b>bgp route selection</b>).
                Fix the last answer by typing: <b>No, it's actually: ...</b>
              </div>
            </div>
            <button class="btn" id="themeCloseBtn">Close</button>
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
        </div>

        <div id="chat" class="chat"></div>
        <div class="small">Enter = send. Shift+Enter = new line.</div>

        <div class="inputbar">
          <textarea id="msg" placeholder="Type a message..."></textarea>
          <button id="sendBtn" class="sendbtn">Send</button>
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
  const themeToggleBtn = document.getElementById("themeToggleBtn");
  const themePanel = document.getElementById("themePanel");
  const themeCloseBtn = document.getElementById("themeCloseBtn");

  const themeNameEl = document.getElementById("themeName");
  const themeIntensityEl = document.getElementById("themeIntensity");
  const applyThemeBtn = document.getElementById("applyThemeBtn");
  const offThemeBtn = document.getElementById("offThemeBtn");

  const STORE_KEY = "machinespirit_chat_v1";

  let lastTopic = "";
  let lastQuestion = "";

  function nowHHMM(){
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,"0");
    const mm = String(d.getMinutes()).padStart(2,"0");
    return `${hh}:${mm}`;
  }

  function loadChat(){
    try{
      const raw = localStorage.getItem(STORE_KEY);
      if(!raw) return [];
      const arr = JSON.parse(raw);
      if(Array.isArray(arr)) return arr;
    }catch(e){}
    return [];
  }

  function saveChat(arr){
    try{ localStorage.setItem(STORE_KEY, JSON.stringify(arr)); }catch(e){}
  }

  function linkify(text){
    const s = (text || "");
    const urlRe = /(https?:\/\/[^\s]+)/g;
    return s.replace(urlRe, (m) => `<a href="${m}" target="_blank" rel="noopener noreferrer">${m}</a>`);
  }

  function appendMessage(role, name, content, when){
    const row = document.createElement("div");
    row.className = "row " + (role === "user" ? "user" : "ai");

    if(role !== "user"){
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
    nm.textContent = name;

    const tm = document.createElement("div");
    tm.textContent = when || nowHHMM();

    meta.appendChild(nm);
    meta.appendChild(tm);
    bubble.appendChild(meta);

    const body = document.createElement("div");
    body.className = "content";
    body.innerHTML = linkify(content);
    bubble.appendChild(body);

    row.appendChild(bubble);

    if(role === "user"){
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
    t = t.replace(/^\s*(what is|what's|define|explain)\s+/i, "").trim();
    t = t.replace(/[?!.]+$/g, "").trim();
    return t;
  }

  function topicFromAnswer(ans){
    const s = (ans || "");

    // Prefer VOX header topic when present
    let m = s.match(/VOX-CAST\s*\/\/\s*([^\n+]+?)\s*\+{3}/i);
    if(m && m[1]){
      const cand = normalizeTopic(m[1]);

      // If the "topic" is actually a correction phrase, ignore it
      if(/^(no|nah|nope|correction|actually)\b/i.test(cand)) return "";
      if(cand.length > 140) return "";
      return cand;
    }

    // Fallback: first reasonable short line
    const lines = s.split("\n").map(x => x.trim()).filter(Boolean);
    for(const line of lines){
      if(line.startsWith("+++")) continue;
      if(/^definition:$/i.test(line)) continue;
      if(/^key points:$/i.test(line)) continue;
      if(/^sources:$/i.test(line)) continue;
      if(/^refusing\b/i.test(line)) continue;

      const cand = normalizeTopic(line);
      if(!cand) continue;
      if(/^(no|nah|nope|correction|actually)\b/i.test(cand)) continue;
      if(cand.length <= 140) return cand;
    }
    return "";
  }

  function extractCorrection(s){
    // normalize smart quotes/dashes so “it’s” works like "it's"
    const t = (s || "")
      .replace(/[“”]/g, '"')
      .replace(/[’]/g, "'")
      .replace(/[–—]/g, "-")
      .trim();

    // no its: ... / no it's: ... / no it is: ...
    let m = t.match(/^(?:no|nah|nope)\s*(?:,|\s)*\s*(?:(?:it\s*is)|(?:it'?s)|(?:its))?\s*(?:actually)?\s*[:\-]\s*([\s\S]+)$/i);
    if(m && m[1]) return m[1].trim();

    // no it's actually <answer> (no colon)
    m = t.match(/^(?:no|nah|nope)\s*(?:,|\s)*\s*(?:(?:it\s*is)|(?:it'?s)|(?:its))?\s*actually\s+([\s\S]+)$/i);
    if(m && m[1]) return m[1].trim();

    // that's wrong, it's <answer>
    m = t.match(/^that'?s\s+wrong\s*(?:,|\s)*(?:(?:it\s*is)|(?:it'?s)|(?:its))?\s*[:\-]?\s*([\s\S]+)$/i);
    if(m && m[1]) return m[1].trim();

    // correction: <answer>
    m = t.match(/^correction\s*[:\-]\s*([\s\S]+)$/i);
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
    const r = await fetch("/api/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme, intensity })
    });
    await r.json().catch(() => ({}));
    await refreshTheme();
    setThemePanel(false);
  }

  async function offTheme(){
    const r = await fetch("/api/theme", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme: "none", intensity: "light" })
    });
    await r.json().catch(() => ({}));
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

    // If user is correcting, do NOT send to /api/ask (brain/junk filters).
    const correction = extractCorrection(text);
    if(correction){
      const topic = normalizeTopic(lastTopic || lastQuestion);
      if(!topic){
        pushAndRender("ai", "MachineSpirit", "I don’t know what to replace yet. Ask a question first, then correct it.");
        return;
      }

      const res = await override(topic, correction);
      if(res && res.ok){
        pushAndRender("ai", "MachineSpirit", `Got it — I replaced my saved answer for "${res.topic}".`);
      }else{
        pushAndRender("ai", "MachineSpirit", `Override failed: ${res.detail || res.error || "unknown error"}`);
      }
      return;
    }

    lastQuestion = normalizeTopic(text);

    const out = await ask(text);
    if(!out || out.ok === false){
      pushAndRender("ai", "MachineSpirit", out.detail || out.error || "API error: failed to ask");
      return;
    }

    const ans = out.answer || out.text || out.response || JSON.stringify(out);
    pushAndRender("ai", "MachineSpirit", ans);

    // Prefer real topic returned by API; fallback to VOX parse; fallback to question
    const apiTopic = normalizeTopic(out.topic || "");
    const parsedTopic = normalizeTopic(topicFromAnswer(ans) || "");
    lastTopic = apiTopic || parsedTopic || lastQuestion;

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
  });

  themeToggleBtn.addEventListener("click", () => setThemePanel(themePanel.style.display === "none"));
  themeCloseBtn.addEventListener("click", () => setThemePanel(false));
  themePill.addEventListener("click", () => setThemePanel(true));

  applyThemeBtn.addEventListener("click", applyTheme);
  offThemeBtn.addEventListener("click", offTheme);

  renderAll();
  refreshTheme();
  setThemePanel(false);
</script>
</body>
</html>
"""
