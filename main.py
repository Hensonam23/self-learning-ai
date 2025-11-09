#!/usr/bin/env python3
from __future__ import annotations
import os
import threading
import time

from brain import Brain
from network.network_server import serve_async, register_web_handler, push, push_voice
from voice_loop import start_voice_thread

HTTP_PORT = int(os.environ.get("MS_HTTP_PORT", "8089"))

web_brain = Brain(mem_path="~/self-learning-ai/data/chat_web.json")
voice_brain = Brain(mem_path="~/self-learning-ai/data/chat_voice.json")

def handle_web_ask(text: str) -> str:
    return web_brain.answer(text)

def handle_voice_ask(text: str) -> str:
    return voice_brain.answer(text)

def main():
    register_web_handler(handle_web_ask)

    push("[WEB] Machine Spirit online. Awaiting your command.")
    serve_async(HTTP_PORT)
    push(f"[WEB] listening on http://0.0.0.0:{HTTP_PORT}")

    shutdown_event = threading.Event()
    start_voice_thread(
        push_cb=lambda msg: push_voice(msg),
        shutdown_event=shutdown_event,
        answer_fn=handle_voice_ask,
    )

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        push("[WEB] [SHUTDOWN] Machine Spirit standing down.")
        shutdown_event.set()

if __name__ == "__main__":
    main()
