import os
import re
import traceback
from typing import Dict, List

from storage.memory import add_learning_summary, add_note

# Optional web search with ddgs
try:
    from ddgs import DDGS

    HAVE_DDGS = True
except Exception:
    HAVE_DDGS = False


def _log(msg: str):
    print(msg, flush=True)


def _search_web(query: str, n: int = 5) -> List[Dict[str, str]]:
    if not HAVE_DDGS:
        _log("[SEARCH] ddgs not installed; skipping web fetch.")
        return []
    out = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(
                query, max_results=n, safesearch="moderate", region="wt-wt"
            ):
                # ddgs result keys vary, normalize
                title = r.get("title") or r.get("heading") or ""
                href = r.get("href") or r.get("url") or ""
                body = r.get("body") or r.get("snippet") or ""
                if title or href or body:
                    out.append({"title": title, "url": href, "snippet": body})
                if len(out) >= n:
                    break
    except Exception as e:
        _log(f"[SEARCH] error: {e}")
        _log(traceback.format_exc())
    return out


CMD_RE = re.compile(
    r"^(?:ms\s+)?(?:(?:/)?(learn|search)|machine\s+spirit\s+(?:learn|search))\s+(.+)$",
    re.I,
)


def handle_intent_or_ack(text: str, *, push_ai_caption):
    """Parse typed or spoken commands and perform learning/search."""
    t = (text or "").strip()
    m = CMD_RE.match(t)
    if m:
        cmd = m.group(1) or ("learn" if "learn" in t.lower() else "search")
        topic = m.group(2).strip()
        if cmd.lower() == "learn":
            _log(f"2025-08-15 00:00:00 [LEARN] topic queued: {topic}")
            push_ai_caption(f"Learning: {topic}")
            _perform_learn(topic)
            return
        else:
            _log(f"[SEARCH] query: {topic}")
            push_ai_caption(f"Searching: {topic}")
            _perform_search(topic)
            return

    # not a command: just acknowledge
    push_ai_caption(t + " — acknowledged.")


def _perform_search(query: str):
    root = os.environ.get("ROOT", "/home/aaron/self-learning-ai")
    mem = os.environ.get("MEMORY_FILE", os.path.join(root, "memory.json"))
    items = _search_web(query, n=5)
    if items:
        try:
            add_learning_summary(mem, f"search: {query}", items)
            print("[MEMORY] saved search results", flush=True)
        except Exception as e:
            print(f"[MEMORY] write error: {e}", flush=True)


def _perform_learn(topic: str):
    root = os.environ.get("ROOT", "/home/aaron/self-learning-ai")
    mem = os.environ.get("MEMORY_FILE", os.path.join(root, "memory.json"))
    items = _search_web(f"{topic} tutorial", n=5)
    summary_lines = [
        f"- {it.get('title','').strip()} :: {it.get('url','').strip()}" for it in items
    ]
    note = f"[LEARN] {topic}\n" + (
        "\n".join(summary_lines) if summary_lines else "(no results)"
    )
    try:
        add_note(mem, note)
    except Exception as e:
        print(f"[MEMORY] write error: {e}", flush=True)
    try:
        if items:
            add_learning_summary(mem, topic, items)
            print("[MEMORY] saved learning summary", flush=True)
    except Exception as e:
        print(f"[MEMORY] write error: {e}", flush=True)
