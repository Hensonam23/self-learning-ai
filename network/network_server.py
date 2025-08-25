# network/network_server.py
from __future__ import annotations
import unicodedata, threading
from collections import deque
from flask import Flask, request, jsonify, Response

# Two independent logs so web and voice aren't mixed
LOG_WEB = deque(maxlen=2000)
LOG_VOICE = deque(maxlen=2000)

def _safe_ascii(s):
    try:
        s = str(s)
    except Exception:
        s = repr(s)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")

def push_web(line: str) -> None:
    s = str(line)
    LOG_WEB.append(s)
    try:
        print(_safe_ascii(s), flush=True)
    except Exception:
        pass

def push_voice(line: str) -> None:
    s = str(line)
    LOG_VOICE.append(s)
    try:
        print(_safe_ascii(s), flush=True)
    except Exception:
        pass

app = Flask(__name__)

# The web chat uses this callback (set by evolve_ai.py)
def _default_on_ask(_text: str) -> str:
    return "OK"
app.config["ON_ASK"] = _default_on_ask

@app.get("/")
def ui_chat():
    # NOTE: plain triple-quoted string (NOT an f-string) so JS braces {} don't break Python parsing.
    html = """<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Machine Spirit — Web Chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0b0f10;color:#d9ffe8;font:15px/1.4 ui-monospace,Consolas,Menlo,monospace}
header{padding:12px 16px;background:#0f1518;border-bottom:1px solid #123}
main{display:flex;flex-direction:column;height:calc(100vh - 52px)}
#log{flex:1;overflow:auto;padding:14px 16px;white-space:pre-wrap;word-wrap:break-word}
#log .user{color:#9cd2ff}
#log .assistant{color:#e7ffb6}
form{display:flex;gap:8px;padding:12px 16px;border-top:1px solid #123;background:#0f1518}
input[type=text]{flex:1;padding:10px 12px;border:1px solid #234;background:#081014;color:#eafff5;border-radius:8px}
button{padding:10px 14px;border:1px solid #2a5;background:#0d221c;color:#d9ffe8;border-radius:8px;cursor:pointer}
a,a:visited{color:#aef}
nav{position:absolute;top:10px;right:16px}
nav a{margin-left:12px}
.mono{font-family:ui-monospace,Consolas,Menlo,monospace}
</style>
</head>
<body>
<header>
  <strong>Machine Spirit — Web Chat</strong>
  <nav class="mono">
    <a href="/voice" target="_blank">Voice Log</a>
  </nav>
</header>
<main>
  <div id="log"></div>
  <form id="f">
    <input id="q" type="text" autocomplete="off" placeholder="Type and hit Enter…" />
    <button type="submit">Send</button>
  </form>
</main>
<script>
const log = document.getElementById('log');
const q   = document.getElementById('q');
const f   = document.getElementById('f');

function esc(s){return s.replace(/[&<>]/g, c=>({ '&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}

async function refresh(){
  const r = await fetch('/log/web');
  const lines = await r.json();
  log.innerHTML = lines.map(x=>{
    let cls = 'assistant';
    if(x.startsWith('> ')) cls = 'user';
    return `<div class="${cls}">${esc(x)}</div>`;
  }).join('');
  log.scrollTop = log.scrollHeight;
}
setInterval(refresh, 600);
refresh();

f.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const text = q.value.trim();
  if(!text) return;
  await fetch('/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
  q.value='';
  setTimeout(refresh, 100);
});
</script>
</body>
</html>"""
    return Response(html, mimetype="text/html")

@app.get("/voice")
def ui_voice():
    html = """<!doctype html><meta charset="utf-8"/>
<title>Machine Spirit — Voice Log</title>
<style>body{margin:0;background:#0b0f10;color:#d9ffe8;font:15px ui-monospace,Consolas,Menlo,monospace}
header{padding:12px 16px;background:#0f1518;border-bottom:1px solid #123}
#log{white-space:pre-wrap;word-wrap:break-word;padding:14px 16px}
.user{color:#9cd2ff}.assistant{color:#e7ffb6}</style>
<header><strong>Voice Log</strong> — live transcript & replies</header>
<pre id="log">loading…</pre>
<script>
async function refresh(){
  const r = await fetch('/log/voice');
  const lines = await r.json();
  const esc = s => s.replace(/[&<>]/g, c=>({ '&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  document.getElementById('log').innerHTML = lines.map(esc).join("\\n");
  window.scrollTo(0,document.body.scrollHeight);
}
setInterval(refresh, 600);
refresh();
</script>"""
    return Response(html, mimetype="text/html")

@app.get("/log/web")
def get_log_web():
    return jsonify(list(LOG_WEB))

@app.get("/log/voice")
def get_log_voice():
    return jsonify(list(LOG_VOICE))

# Back-compat single log endpoint (returns web log)
@app.get("/log")
def get_log_compat():
    return Response("\n".join(LOG_WEB), mimetype="text/plain")

@app.get("/healthz")
def health():
    return Response("OK", mimetype="text/plain")

@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or request.form or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400

    # Echo user's input into WEB log (chat keeps both sides)
    push_web("> " + text)
    reply = app.config["ON_ASK"](text)  # call the web brain
    for ln in str(reply).splitlines():
        push_web(ln)
    return jsonify({"ok": True, "reply": reply})

def serve_async(port: int):
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    push_web(f"[HTTP] listening on http://0.0.0.0:{port}")
