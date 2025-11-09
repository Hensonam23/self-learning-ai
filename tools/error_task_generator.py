#!/usr/bin/env python3
"""Generate code edit tasks from logged errors using an LLM."""

from __future__ import annotations
import json
import os
import urllib.request
from typing import Dict, Any

from storage.memory import load_memory, queue_learning_item, append_note

API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _call_llm(prompt: str) -> str:
    if not API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    data = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
    obj = json.loads(body)
    return obj["choices"][0]["message"]["content"]


def main() -> None:
    mem = load_memory()
    for err in mem.get("errors", []):
        prompt = (
            "Given the following error from an AI system, suggest a code change to fix it.\n"
            f"Context: {err.get('context')}\n"
            f"Message: {err.get('message')}\n"
            "Return JSON with keys file, search, replace, commit."
        )
        try:
            reply = _call_llm(prompt)
            task: Dict[str, Any] = json.loads(reply)
            task["topic"] = "code"
            queue_learning_item(task)
            append_note(f"queued code task for {task.get('file')}")
        except Exception as exc:  # pragma: no cover
            append_note(f"error-task-generator failed: {exc}")


if __name__ == "__main__":
    main()
