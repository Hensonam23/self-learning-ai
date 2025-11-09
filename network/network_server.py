#!/usr/bin/env python3
from __future__ import annotations
import threading
from typing import Callable, Dict, List

from flask import Flask, request, jsonify

app = Flask(__name__)

# In-memory logs
_logs: Dict[str, List[dict]] = {
    "web": [],
    "voice": [],
}
_lock = threading.Lock()

def _append(stream: str, role: str, text: str) -> None:
    stream = stream if stream in _logs else "web"
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        _logs[stream].append({"role": role, "text": text})
        if len(_logs[stream]) > 200:
            _logs[stream] = _logs[stream][-200:]

def get_log(stream: str) -> List[dict]:
    stream = stream if stream in _logs else "web"
    with _lock:
        return list(_logs[stream])

# ----- public helpers -----

def push(message: str, stream: str = "web") -> None:
    """System-style message to web log + console."""
    line = (message or "").rstrip()
    if not line:
        return
    print(line, flush=True)
    _append(stream, "system", line)

def push_web(message: str) -> None:
    push(message, "web")

def push_voice(message: str) -> None:
    push(message, "voice")

# ----- web chat wiring -----

_on_web_ask: Callable[[str], str] | None = None

def register_web_handler(fn: Callable[[str], str]) -> None:
    global _on_web_ask
    _on_web_ask = fn

# ----- HTTP routes -----

@app.route("/", methods=["GET"])
def index() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Machine Spirit</title>
  <style>
    body {
      margin:0; padding:0;
      background:#000; color:#0f0;
      font-family:Consolas,Monaco,monospace;
    }
    #wrap {
      display:flex; flex-direction:column;
      height:100vh;
    }
    #header {
      padding:8px 14px;
      border-bottom:1px solid #0f0;
      font-size:12px;
      letter-spacing:2px;
      text-transform:uppercase;
    }
    #chat {
      flex:1;
      padding:12px;
      overflow-y:auto;
      font-size:12px;
    }
    .line { margin:4px 0; }
    .role {
      display:inline-block;
      padding:2px 6px;
      border:1px solid #0f0;
      border-radius:6px;
      margin-right:6px;
      font-size:9px;
    }
    .you { color:#0f0; }
    .spirit { color:#000; background:#0f0; }
    .msg {
      white-space:pre-wrap;
      word-wrap:break-word;
    }
    #inputBar {
      display:flex;
      border-top:1px solid #0f0;
    }
    #input {
      flex:1;
      background:#000;
      color:#0f0;
      border:none;
      padding:10px;
      font-family:inherit;
      font-size:12px;
      outline:none;
    }
    #send {
      width:120px;
      background:#000;
      color:#0f0;
      border:none;
      border-left:1px solid #0f0;
      cursor:pointer;
      font-size:11px;
      text-transform:uppercase;
    }
    #send:hover { background:#020; }
  </style>
</head>
<body>
<div id="wrap">
  <div id="header">[WEB] MACHINE SPIRIT // ASK CONSOLE</div>
  <div id="chat"></div>
  <div id="inputBar">
    <input id="input" placeholder="Issue your command..." autocomplete="off" />
    <button id="send">SEND</button>
  </div>
</div>
<script>
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');

function addLine(roleLabel, cssRole, text) {
  const line = document.createElement('div');
  line.className = 'line';
  const badge = document.createElement('span');
  badge.className = 'role ' + cssRole;
  badge.textContent = roleLabel;
  const msg = document.createElement('span');
  msg.className = 'msg';
  msg.textContent = ' ' + text;
  line.appendChild(badge);
  line.appendChild(msg);
  chat.appendChild(line);
  chat.scrollTop = chat.scrollHeight;
}
function addUser(text){ addLine('YOU','you',text); }
function addSpirit(text){ addLine('SPIRIT','spirit',text); }

async function send() {
  const text = input.value.trim();
  if (!text) return;
  addUser(text);
  input.value = '';
  try {
    const res = await fetch('/ask',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})
    });
    const data = await res.json();
    if (data && data.reply) addSpirit(data.reply);
  } catch(e){
    addSpirit('[link severed]');
  }
}
sendBtn.onclick = send;
input.addEventListener('keydown',e=>{ if(e.key==='Enter') send(); });

// load existing history once
(async()=>{
  try{
    const res = await fetch('/log/web');
    const data = await res.json();
    (data||[]).forEach(it=>{
      if(!it.text) return;
      if(it.role==='user') addUser(it.text);
      else if(it.role==='assistant') addSpirit(it.text);
      else addSpirit(it.text);
    });
  }catch(e){}
})();
</script>
</body>
</html>
"""

@app.route("/ask", methods=["POST"])
def ask() -> tuple:
    global _on_web_ask
    if _on_web_ask is None:
        return jsonify({"reply": "Brain not ready."}), 500
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"reply": ""})
    _append("web", "user", text)
    reply = _on_web_ask(text)
    _append("web", "assistant", reply)
    return jsonify({"reply": reply})

@app.route("/log/<stream>", methods=["GET"])
def log_route(stream: str):
    return jsonify(get_log(stream))

def serve_async(port: int) -> None:
    t = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False,
        ),
        name="http",
        daemon=True,
    )
    t.start()

if __name__ == "__main__":
    serve_async(8089)
    import time
    while True:
        time.sleep(1)
