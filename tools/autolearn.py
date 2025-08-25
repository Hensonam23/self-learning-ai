#!/usr/bin/env python3
"""
Autonomous overnight learner.

- Pulls topics from storage.memory.learning_queue, OR invents its own topics
  when the queue is empty (uses curiosity seeds + expands from prior knowledge).
- Searches the open web (DuckDuckGo HTML endpoint), respects robots.txt,
  rate-limits requests, fetches a few pages per topic, extracts text, and
  writes summaries + sources into storage.memory. Logs errors to notes.
- Caches fetched pages to avoid hammering sites, and records progress to a log.

No external deps; uses urllib + simple HTML stripping so it runs on a fresh Pi.
If BeautifulSoup is available, it will use it automatically for better parsing.
"""

from __future__ import annotations
import os, re, sys, time, json, hashlib, random, urllib.parse, urllib.request, urllib.error
import urllib.robotparser as robotparser
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

# --- project path fix when invoked directly ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from storage.memory import (  # noqa: E402
    append_note,
    list_learning_queue,
    pop_learning_queue,
    add_knowledge,
)

UA = "MachineSpiritAutolearn/1.0 (+https://example.invalid) Python-urllib"
DDG_HTML = "https://duckduckgo.com/html/?{query}"  # public HTML results
CACHE_DIR = os.path.expanduser("~/self-learning-ai/.cache/pages")
LOG_DIR = os.path.expanduser("~/self-learning-ai/logs")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "autolearn.log")

DEFAULTS = {
    "pages_per_topic": int(os.environ.get("AUTOLEARN_PAGES_PER_TOPIC", "3")),
    "max_chars_per_page": int(os.environ.get("AUTOLEARN_MAX_CHARS", "40000")),
    "rate_limit_sec": float(os.environ.get("AUTOLEARN_RATE_LIMIT", "1.2")),
    "hours": float(os.environ.get("AUTOLEARN_HOURS", "6")),
    "respect_robots": os.environ.get("AUTOLEARN_RESPECT_ROBOTS", "1") != "0",
    "dedup_window": 50,  # recent URLs we will avoid repeating
}

# Try to enable BeautifulSoup if present (optional)
try:
    from bs4 import BeautifulSoup  # type: ignore
    HAVE_BS4 = True
except Exception:
    BeautifulSoup = None  # type: ignore
    HAVE_BS4 = False


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [AUTOLEARN] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _strip_html(html: str) -> str:
    """Very simple HTML -> text fallback (BeautifulSoup if available)."""
    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    # fallback regex stripper
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<[^>]+>", " ", html)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n+", "\n", text).strip()


def _sentence_split(text: str) -> List[str]:
    # rudimentary sentence splitter
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return [p.strip() for p in parts if p.strip()]


def _summarize(text: str, limit_sentences: int = 6) -> str:
    """Keywordy, extremely light extractive summary."""
    sentences = _sentence_split(text)
    if not sentences:
        return ""
    # build crude frequency map
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    freq: Dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    # score each sentence
    def score(s: str) -> float:
        tokens = re.findall(r"[a-zA-Z]{3,}", s.lower())
        return sum(freq.get(t, 0) for t in tokens) / (len(tokens) + 1)

    ranked = sorted(sentences, key=score, reverse=True)[: max(3, limit_sentences)]
    # keep original order for readability
    pickset = set(ranked)
    ordered = [s for s in sentences if s in pickset][:limit_sentences]
    return " ".join(ordered)


def _http_get(url: str, max_bytes: int) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read(max_bytes + 1)
        return data[:max_bytes]
    # caller handles exceptions


def _cache_path(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.html")


def _fetch_text(url: str, max_chars: int, rp_cache: Dict[str, robotparser.RobotFileParser], rate_limit: float, respect_robots: bool) -> Optional[str]:
    # robots.txt check
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if respect_robots:
        if base not in rp_cache:
            rp = robotparser.RobotFileParser()
            rp.set_url(urllib.parse.urljoin(base, "/robots.txt"))
            try:
                rp.read()
            except Exception:
                pass
            rp_cache[base] = rp
        rp = rp_cache[base]
        try:
            allowed = rp.can_fetch(UA, url) if rp.default_entry else True
        except Exception:
            allowed = True
        if not allowed:
            _log(f"robots.txt disallow: {url}")
            return None

    # cache
    cp = _cache_path(url)
    if os.path.exists(cp):
        try:
            html = open(cp, "rb").read().decode("utf-8", "ignore")
            return _strip_html(html)
        except Exception:
            pass

    time.sleep(rate_limit)
    try:
        raw = _http_get(url, max_chars * 3)  # raw may be larger; we strip later
        if not raw:
            return None
        html = raw.decode("utf-8", "ignore")
        try:
            with open(cp, "wb") as fh:
                fh.write(html.encode("utf-8", "ignore"))
        except Exception:
            pass
        return _strip_html(html)
    except urllib.error.HTTPError as e:
        _log(f"HTTPError {e.code} on {url}")
        return None
    except Exception as e:
        _log(f"Fetch error on {url}: {e}")
        return None


def search_duckduckgo(query: str, n: int = 10, rate_limit: float = DEFAULTS["rate_limit_sec"]) -> List[Tuple[str, str]]:
    q = urllib.parse.urlencode({"q": query})
    url = DDG_HTML.format(query=q)
    time.sleep(rate_limit)
    try:
        raw = _http_get(url, 2_000_000)
        if not raw:
            return []
        html = raw.decode("utf-8", "ignore")
        if HAVE_BS4:
            soup = BeautifulSoup(html, "html.parser")
            links = []
            for a in soup.select("a.result__a"):
                href = a.get("href")
                if not href:
                    continue
                # DDG HTML often gives direct links
                links.append((a.get_text(" ", strip=True), href))
            return links[:n]
        # fallback regex: find anchors with class result__a
        out = []
        for m in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
            link = urllib.parse.unquote(m.group(1))
            title = re.sub(r"(?is)<[^>]+>", " ", m.group(2))
            title = re.sub(r"\s+", " ", title).strip()
            out.append((title, link))
            if len(out) >= n:
                break
        return out
    except Exception as e:
        _log(f"search error: {e}")
        return []


def _clean_topic(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())


def _invent_topics(prior: List[str], k: int = 3) -> List[str]:
    """Generate curiosity topics when queue is empty."""
    seeds = [
        "python async patterns", "designing resilient systems",
        "voice activity detection strategies", "knowledge graph basics",
        "neural vocoders vs classic TTS", "privacy-preserving logging",
        "robotics planning intro", "linux audio plumbing (ALSA/Pulse/JACK)",
        "websocket reliability", "search ranking signals",
        "error correction in self-learning systems",
    ]
    # expand from prior knowledge words
    mixers = ["how does", "why does", "compare", "best practices", "tradeoffs of"]
    picks = []
    pool = list(seeds)
    for p in prior[-12:]:
        for m in mixers:
            pool.append(f"{m} {p}")
    random.shuffle(pool)
    for i in pool:
        if len(picks) >= k:
            break
        picks.append(i)
    return picks


def learn_one(topic: str, pages_per_topic: int = DEFAULTS["pages_per_topic"]) -> bool:
    topic = _clean_topic(topic)
    if not topic:
        return False

    _log(f"LEARN start: {topic}")
    try:
        hits = search_duckduckgo(topic, n=max(8, pages_per_topic * 3))
        if not hits:
            append_note(f"AUTOLEARN: no results for '{topic}'", tags=["autolearn", "warn"])
            return False

        rp_cache: Dict[str, robotparser.RobotFileParser] = {}
        seen_hosts: Dict[str, int] = {}
        seen_recent_urls: List[str] = []
        texts: List[str] = []
        sources: List[str] = []

        for title, url in hits:
            try:
                u = urllib.parse.urlparse(url)
                if not (u.scheme in ("http", "https") and u.netloc):
                    continue
                host = u.netloc
            except Exception:
                continue

            # de-dup per host so we sample diverse sources
            seen_hosts[host] = seen_hosts.get(host, 0) + 1
            if seen_hosts[host] > 2:
                continue

            text = _fetch_text(
                url,
                max_chars=DEFAULTS["max_chars_per_page"],
                rp_cache=rp_cache,
                rate_limit=DEFAULTS["rate_limit_sec"],
                respect_robots=DEFAULTS["respect_robots"],
            )
            if not text or len(text) < 400:
                continue
            if url in seen_recent_urls:
                continue
            seen_recent_urls.append(url)
            texts.append(text)
            sources.append(url)
            if len(texts) >= pages_per_topic:
                break

        if not texts:
            append_note(f"AUTOLEARN: fetched none for '{topic}'", tags=["autolearn", "warn"])
            return False

        combined = "\n\n".join(texts)
        summary = _summarize(combined, limit_sentences=7)
        if not summary:
            # fallback to first 800 chars
            summary = (combined[:800] + "…") if len(combined) > 800 else combined

        add_knowledge(topic=topic, summary=summary, sources=sources, meta={
            "method": "ddg-crawl",
            "pages": len(texts),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        append_note(f"AUTOLEARN: learned '{topic}' from {len(sources)} sources", tags=["autolearn"])
        _log(f"LEARN done: {topic} (pages={len(sources)})")
        return True

    except Exception as e:
        append_note(f"AUTOLEARN ERROR for '{topic}': {e}", tags=["autolearn", "error"])
        _log(f"ERROR learning '{topic}': {e}")
        return False


def run_loop(hours: float = DEFAULTS["hours"]) -> None:
    stop_at = datetime.utcnow() + timedelta(hours=hours)
    recent_topics: List[str] = []
    successes = 0
    failures = 0

    while datetime.utcnow() < stop_at:
        # prefer explicit queued topics
        queued = list_learning_queue()
        if queued:
            topic = queued[0]["topic"]
            # consume the queue head
            _ = pop_learning_queue()
        else:
            topic = random.choice(_invent_topics(recent_topics or []))

        ok = learn_one(topic)
        (successes if ok else failures)
        if ok:
            recent_topics.append(topic)
            if len(recent_topics) > 40:
                recent_topics = recent_topics[-40:]

        # gentle idle pause between topics
        time.sleep(2.0)

    _log(f"SESSION finished: successes={successes} failures={failures}")


def main():
    hours = DEFAULTS["hours"]
    # simple flag parser for --hours
    for i, a in enumerate(sys.argv[1:]):
        if a == "--hours" and i + 2 <= len(sys.argv[1:]):
            try:
                hours = float(sys.argv[1:][i + 1])
            except Exception:
                pass
    _log(f"START (hours={hours})")
    run_loop(hours=hours)


if __name__ == "__main__":
    main()
