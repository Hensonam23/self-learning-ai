#!/usr/bin/env python3

"""
web_answer_engine.py

Standalone web research engine for the Machine Spirit.

- Uses DuckDuckGo Search (duckduckgo_search) to find pages.
- Fetches HTML with requests.
- Extracts main text with BeautifulSoup.
- Creates a simple extractive summary (pick a few decent sentences).

No external AI APIs. This is slow and imperfect, but fully local logic.
"""

from __future__ import annotations
from typing import List

import re
import requests
from duckduckgo_search import DDGS  # pip install duckduckgo_search
from bs4 import BeautifulSoup       # pip install beautifulsoup4


USER_AGENT = (
    "MachineSpirit/0.1 (+https://example.local; running on Raspberry Pi; "
    "standalone research agent)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Core web helpers
# ---------------------------------------------------------------------------

def search_web(query: str, max_results: int = 3) -> List[dict]:
    """
    Use DuckDuckGo to get some search results.

    Returns a list of dicts with at least 'href' or 'url' keys.
    """
    results: List[dict] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            # r is typically a dict with keys like title, href, body
            results.append(r)
    return results


def fetch_url(url: str, timeout: int = 12) -> str:
    """
    Fetch a URL and return its HTML content as text.
    """
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def extract_main_text(html: str) -> str:
    """
    Strip scripts/styles, then return the visible text.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def summarize_text(text: str, max_sentences: int = 5) -> str:
    """
    Very simple extractive summarizer:

    - Normalize whitespace
    - Split into sentences
    - Keep the first few that are not too short or too long

    This is *not* smart like an LLM, but good enough for reference notes.
    """
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "I tried to read a page, but it did not contain useful text."

    sentences = re.split(r"(?<=[.!?])\s+", text)
    if not sentences:
        return text[:400]

    selected = []
    for s in sentences:
        s = s.strip()
        if len(s) < 40:
            continue
        if len(s) > 400:
            continue
        selected.append(s)
        if len(selected) >= max_sentences:
            break

    if not selected:
        selected = sentences[:max_sentences]

    return " ".join(selected)


# ---------------------------------------------------------------------------
# Public API used by the research worker
# ---------------------------------------------------------------------------

def summarize_url(url: str) -> str:
    """
    Fetch a URL and return a short summary of its contents.
    """
    try:
        html = fetch_url(url)
        text = extract_main_text(html)
        summary = summarize_text(text)
        return summary
    except Exception as e:
        return f"I tried to fetch and summarize that URL but ran into an error: {e!r}"


def answer_topic(question: str) -> str:
    """
    Research a general topic:

    - Search the web for the topic
    - Try a few result URLs
    - Return a short summary from the first one that works
    """
    query = f"{question} explanation simple terms"
    try:
        results = search_web(query, max_results=3)
    except Exception as e:
        return f"I tried to search the web for that topic but ran into an error: {e!r}"

    if not results:
        return "I tried to search the web for that topic but could not find a useful public page."

    for r in results:
        url = r.get("href") or r.get("url")
        if not url:
            continue

        try:
            html = fetch_url(url)
            text = extract_main_text(html)
            summary = summarize_text(text)
            return summary + f"\n\n[Learned from: {url}]"
        except Exception:
            # Try the next result if this one fails
            continue

    return "I tried several web pages but could not extract a clear explanation."
