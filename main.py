import os
import queue
import signal
import sys
import threading
import time
from typing import Tuple

from audio.audio_processing import audio_input_worker
from audio.tts import say as tts_say
from display.display_manager import (display_interface, init_vu,
                                     plan_text_envelope)
from learning_shim import handle_intent_or_ack
from network.network_server import start_server


# -------- Config from .env with defaults ----------
def _getenv(name, default=None, cast=str):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return cast(v)
    except Exception:
        return v


ROOT = os.getcwd()
HTTP_PORT = int(_getenv("HTTP_PORT", 8089))
MEMORY_FILE = _getenv("MEMORY_FILE", os.path.join(ROOT, "memory.json"))
BACKGROUND_PATH = _getenv("BACKGROUND_PATH", os.path.join(ROOT, "background.png"))
SCREEN_SIZE_STR = _getenv("SCREEN_SIZE", "800,480")
SCREEN_SIZE = (
    tuple(int(x) for x in SCREEN_SIZE_STR.split(","))
    if isinstance(SCREEN_SIZE_STR, str)
    else (800, 480)
)
FPS = int(_getenv("FPS", 60))
MIC_PREFERRED = _getenv("MIC_PREFERRED_NAME", "")
LANGUAGE = _getenv("LANGUAGE", "en-US")
VOSK_MODEL_PATH = _getenv("VOSK_MODEL_PATH", "")
DEBUG_AUDIO = bool(int(_getenv("DEBUG_AUDIO", "0")))
MS_HTTP_TOKEN = _getenv("MS_HTTP_TOKEN", "")

# -------- Display VU config ----------
VU_CFG = {
    "SCREEN_SIZE": SCREEN_SIZE,
    "FPS": FPS,
    "BACKGROUND_PATH": BACKGROUND_PATH,
    "TITLE_SIZE": 18,
    "BODY_SIZE": 16,
    "COLOR_WHITE": (230, 235, 240),
    "COLOR_SHADOW": (0, 0, 0),
    "COLOR_GREEN": (60, 200, 140),
    "WAVE_PIXELS": 460,
    "WAVE_VISUAL_SCALE": 0.38,
    "MAX_WAVE_DRAW_PX": 24,
    "SCROLL_SPEED_BASE": 128,
    "CYCLES1_RANGE": (2.2, 4.8),
    "CYCLES2_RANGE": (6.0, 10.8),
}


# -------- App state ----------
class State:
    def __init__(self):
        self._best = ""
        self._target = "the machine spirit watches and learns"
        self._lock = threading.Lock()

    def set_best(self, s: str):
        with self._lock:
            self._best = s

    def set_target(self, s: str):
        with self._lock:
            self._target = s

    def append_to_target(self, s: str):
        with self._lock:
            self._target += " " + s

    def get_status(self) -> Tuple[str, str]:
        with self._lock:
            return self._best, self._target

    def get_target(self) -> str:
        with self._lock:
            return self._target


state = State()
ai_caption_q: "queue.Queue[str]" = queue.Queue(maxsize=256)


# We wrap the UI push to also do VU plan + speak
def _push_ai_caption_ui(text: str):
    try:
        ai_caption_q.put_nowait(text)
    except queue.Full:
        try:
            ai_caption_q.get_nowait()
            ai_caption_q.put_nowait(text)
        except Exception:
            pass


def push_ai_caption(text: str):
    text = (text or "").strip()
    if not text:
        return
    # Plan envelope for the on-screen VU
    try:
        plan_text_envelope(text)
    except Exception as e:
        print(f"[VU] plan error: {e}", flush=True)
    # Speak via Pulse (non-blocking)
    try:
        tts_say(text)
    except Exception as e:
        print(f"[TTS] say error: {e}", flush=True)
    # Show on UI
    _push_ai_caption_ui(text)


talking_event = threading.Event()  # (reserved for future sync)
shutdown_event = threading.Event()


def on_recognized(text: str):
    print(f"Recognized: {text}", flush=True)
    try:
        handle_intent_or_ack(text, push_ai_caption=push_ai_caption)
    except Exception as e:
        print(f"[INTENT] error: {e}", flush=True)


# -------- Thread starters ----------
def _start_display():
    init_vu(VU_CFG)
    display_interface(
        state, ai_caption_q, talking_event, shutdown_event, push_ai_caption
    )


def _start_network():
    # start_server(push_ai_caption, ...) supports token & endpoints
    start_server(push_ai_caption, port=HTTP_PORT, shutdown_event=shutdown_event)


def _start_audio():
    audio_input_worker(
        shutdown_event,
        on_text=on_recognized,
        mic_preferred_name=MIC_PREFERRED,
        debug_audio=DEBUG_AUDIO,
        language=LANGUAGE,
        vosk_model_path=VOSK_MODEL_PATH,
    )


def _start_http(push_ai_caption, shutdown_event, cfg, enqueue_learn, enqueue_search):
    # Late import to use the updated server
    from network.network_server import start_server

    try:
        start_server(
            push_ai_caption,
            port=cfg["HTTP_PORT"],
            shutdown_event=shutdown_event,
            learn_func=enqueue_learn,
            search_func=enqueue_search,
        )
    except Exception as e:
        print(f"HTTP server error: {e}")


def _graceful_stop(signum, frame):
    print("Shutting down… (signal received)", flush=True)
    shutdown_event.set()


def main():
    # Announce on boot so you can hear the device route
    push_ai_caption("By the Omnissiah, systems online.")

    signal.signal(signal.SIGTERM, _graceful_stop)
    signal.signal(signal.SIGINT, _graceful_stop)

    threads = [
        threading.Thread(target=_start_display, name="Display", daemon=True),
        threading.Thread(target=_start_network, name="HTTP", daemon=True),
        threading.Thread(target=_start_audio, name="Audio", daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        while not shutdown_event.is_set():
            time.sleep(0.2)
    finally:
        print("Goodbye.", flush=True)


if __name__ == "__main__":
    main()
