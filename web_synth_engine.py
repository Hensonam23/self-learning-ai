#!/usr/bin/env python3
from __future__ import annotations

"""
web_synth_engine

Small "research + thought" helper for Machine Spirit.

Behavior:
- Extracts the topic from the question.
- Tries Wikipedia summary.
- Optionally looks at a few DuckDuckGo HTML snippets.
- Synthesizes a short, direct explanation (1–4 sentences),
  in its own words, not a big pasted search result.
- If sources are weak, still returns a reasoned best-effort answer.
"""

import html
import json
import re
import time
import urllib.parse
import urllib.request
from typing import List

UA = "MachineSpirit/1.0 (+local-assistant)"

WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
DDG_HTML = "https://duckduckgo.com/html/?{query}"

HTTP_TIMEOUT = 8


# ---------- HTTP ----------

def _http_get(url: str, max_bytes: int = 200_000) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = resp.read(max_bytes)
    return data.decode("utf-8", "ignore")


# ---------- text helpers ----------

def _clean_topic_from_question(q: str) -> str:
    q = (q or "").strip()
    low = q.lower()
    m = re.match(r"^(what\s+is|what's|who\s+is|define|explain|tell\s+me\s+about)\s+(.*)$", low)
    topic = m.group(2) if m else low
    topic = topic.strip(" ?!.,")
    topic = re.sub(r"^(a|an|the)\s+", "", topic)
    return topic.strip() or q.strip()


def _sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return [p.strip() for p in parts if p.strip()]


def _pick_def_sentence(sentences: List[str]) -> str:
    if not sentences:
        return ""
    for s in sentences:
        if re.search(r"\b(is|are|refers to|consists of|means)\b", s[:80].lower()):
            return s
    return sentences[0]


# ---------- sources ----------

def _wiki_summary(topic: str) -> str:
    try:
        title = urllib.parse.quote(topic.replace(" ", "_"))
        url = WIKI_SUMMARY.format(title=title)
        raw = _http_get(url)
        data = json.loads(raw)
        extract = (data.get("extract") or "").strip()
        return extract
    except Exception:
        return ""


def _ddg_snippets(topic: str, max_snips: int = 2) -> List[str]:
    try:
        q = urllib.parse.urlencode({"q": topic})
        url = DDG_HTML.format(query=q)
        html_txt = _http_get(url, max_bytes=300_000)
    except Exception:
        return []

    snippets: List[str] = []

    # Look for result__snippet blocks
    for m in re.finditer(r'class="result__snippet"[^>]*>(.*?)</a>', html_txt, re.I | re.S):
        snippet_html = m.group(1) or ""
        snippet = re.sub(r"<[^>]+>", " ", snippet_html)
        snippet = html.unescape(snippet)
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if snippet:
            snippets.append(snippet)
        if len(snippets) >= max_snips:
            break

    return snippets


# ---------- synthesis ----------

def _infer_uses(text: str) -> List[str]:
    text_low = text.lower()
    uses = []
    if any(w in text_low for w in ["record", "broadcast", "podcast", "call"]):
        uses.append("recording and communication")
    if any(w in text_low for w in ["music", "instrument", "song", "audio"]):
        uses.append("music and audio work")
    if any(w in text_low for w in ["game", "gaming"]):
        uses.append("gaming")
    if any(w in text_low for w in ["measure", "monitor", "sensor"]):
        uses.append("measurement or monitoring")
    # make unique
    seen = set()
    result = []
    for u in uses:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _synth(topic: str, wiki_text: str, ddg_snips: List[str]) -> str:
    topic_clean = topic.strip()
    base = ""

    if wiki_text:
        sents = _sentences(wiki_text)
        lead = _pick_def_sentence(sents)
        # trim overly long leads
        if len(lead) > 260:
            # cut at last full stop before 260 or just slice
            cut = lead[:260]
            last = cut.rfind(".")
            if last > 40:
                lead = cut[: last + 1]
            else:
                lead = cut + "..."
        base = lead

    # Use snippets only as hints, not verbatim dumps
    hint_text = " ".join(ddg_snips)
    combined_source = (wiki_text + " " + hint_text).strip()

    if not base and combined_source:
        # try to craft a definition from combined text
        sents = _sentences(combined_source)
        lead = _pick_def_sentence(sents)
        base = lead

    if base:
        # Make sure we explicitly anchor on the topic name
        if not re.match(rf"^{re.escape(topic_clean)}\b", base, flags=re.I):
            base = f"{topic_clean.capitalize()} is {base.lstrip().capitalize()}"
        # Add a human-style second sentence for common uses
        uses = _infer_uses(combined_source)
        if uses:
            base = base.rstrip()
            base += f" It is commonly used for " + ", ".join(uses) + "."
        return base.strip()

    # If everything failed, still respond thoughtfully
    return (
        f"{topic_clean.capitalize()} is not clearly described in my current sources, "
        "but it can be understood by looking at how people use the term in context. "
        "If you narrow it down, I can give a more precise breakdown."
    )


# ---------- public API ----------

def ask_web(question: str) -> str:
    q = (question or "").strip()
    if not q:
        return "I am listening."

    topic = _clean_topic_from_question(q)

    # Light delay so we don't hammer endpoints in a tight loop
    time.sleep(0.25)

    wiki_text = _wiki_summary(topic)
    ddg_snips = _ddg_snippets(topic)

    # If nothing, try a simplified topic once
    if not (wiki_text or ddg_snips):
        simpler = re.sub(r"[^a-zA-Z0-9\s]", " ", topic)
        simpler = re.sub(r"\s+", " ", simpler).strip()
        if simpler and simpler != topic:
            wiki_text = _wiki_summary(simpler)
            if not wiki_text:
                ddg_snips = _ddg_snippets(simpler)
            if wiki_text or ddg_snips:
                topic = simpler

    return _synth(topic, wiki_text, ddg_snips)
