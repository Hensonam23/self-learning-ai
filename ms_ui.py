#!/usr/bin/env python3
import os
import html
import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse

APP_NAME = "MachineSpirit UI"

MS_API_BASE = os.environ.get("MS_API_BASE", "http://127.0.0.1:8010").rstrip("/")
MS_API_KEY = (os.environ.get("MS_API_KEY") or "").strip()

app = FastAPI(title=APP_NAME)


def page_template(question: str = "", answer: str = "", raw: str = "") -> str:
    q = html.escape(question or "")
    a = html.escape(answer or "")
    r = html.escape(raw or "")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{APP_NAME}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; }}
    h1 {{ margin-bottom: 8px; }}
    .muted {{ color: #666; }}
    textarea {{ width: 100%; max-width: 900px; height: 120px; }}
    .btn {{ padding: 8px 14px; margin-top: 10px; }}
    .card {{
      width: 100%;
      max-width: 900px;
      background: #111;
      color: #eee;
      padding: 18px;
      border-radius: 12px;
      white-space: pre-wrap;
      line-height: 1.35;
      margin-top: 14px;
    }}
    details {{ max-width: 900px; margin-top: 12px; }}
    .err {{ color: #b00020; font-weight: bold; }}
  </style>
</head>
<body>
  <h1>{APP_NAME}</h1>
  <div class="muted">API: {html.escape(MS_API_BASE)}</div>

  <form method="post" action="/ui/ask">
    <div style="margin-top:16px;"><b>Ask:</b></div>
    <textarea name="text" placeholder="Example: nat, rfc 1918, subnet mask">{q}</textarea><br/>
    <button class="btn" type="submit">Ask</button>
  </form>

  <h2 style="margin-top:24px;">Answer</h2>
  <div class="card">{a if a else "No answer yet."}</div>

  <details>
    <summary>Raw response</summary>
    <div class="card">{r if r else ""}</div>
  </details>

  <p class="muted">Tip: If asking fails, check the API key and that machinespirit-api.service is running.</p>
</body>
</html>"""


@app.get("/ui/ask", response_class=HTMLResponse)
async def ui_get() -> HTMLResponse:
    return HTMLResponse(page_template())


@app.post("/ui/ask", response_class=HTMLResponse)
async def ui_post(request: Request, text: str = Form(...)) -> HTMLResponse:
    question = (text or "").strip()
    if not question:
        return HTMLResponse(page_template("", "Please type a question.", ""))

    headers = {}
    if MS_API_KEY:
        headers["x-api-key"] = MS_API_KEY

    # Use the clean endpoint that returns { ok, topic, answer }
    url = f"{MS_API_BASE}/ask"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json={"text": question})
        raw = resp.text

        if resp.status_code != 200:
            return HTMLResponse(page_template(question, f"ERROR {resp.status_code}: {raw}", raw))

        data = resp.json()
        ans = (data.get("answer") or "").strip()
        if not ans:
            ans = "(No answer returned.)"
        return HTMLResponse(page_template(question, ans, raw))

    except Exception as e:
        return HTMLResponse(page_template(question, f"ERROR: {e}", ""))
