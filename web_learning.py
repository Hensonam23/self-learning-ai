#!/usr/bin/env python3
# web_learning.py
from __future__ import annotations

import json
import re
from typing import Optional, Tuple
from urllib.parse import quote

import urllib.request

UA = "MachineSpirit/1.0 (+local)"

def _http_json(url: str, timeout: float = 8.0) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    try:
        return json.loads(data.decode("utf-8", errors="ignore"))
    except Exception:
        return None

def _trim_sentences(text: str, max_sent: int = 4, max_chars: int = 900) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    out, total = [], 0
    for p in parts:
        if not p:
            continue
        if total + len(p) > max_chars or len(out) >= max_sent:
            break
        out.append(p)
        total += len(p) + 1
    return " ".join(out) if out else (text[:max_chars] if text else "")

def fetch_wikipedia_summary(topic: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Uses Wikipedia REST summary (no key). Returns (summary, source_url) or (None, None).
    """
    if not topic:
        return None, None
    slug = quote(topic.replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
    data = _http_json(url)
    if not data:
        return None, None
    extract = data.get("extract")
    if not extract:
        return None, None
    page_url = None
    try:
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page") or data.get("content_urls", {}).get("mobile", {}).get("page")
    except Exception:
        page_url = None
    return _trim_sentences(extract, 3, 700), page_url

def fetch_ddg_ia(topic: str) -> Tuple[Optional[str], Optional[str]]:
    """
    DuckDuckGo instant answer JSON (no key). Not exhaustive, but handy.
    """
    q = quote(topic)
    url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
    data = _http_json(url)
    if not data:
        return None, None
    text = (data.get("AbstractText") or "").strip()
    if not text:
        return None, None
    src_url = (data.get("AbstractURL") or "").strip() or None
    return _trim_sentences(text, 3, 700), src_url

def fetch_best_summary(topic: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Try Wikipedia first, then DDG IA.
    """
    s, u = fetch_wikipedia_summary(topic)
    if s:
        return s, u
    return fetch_ddg_ia(topic)
