#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time

import speech_recognition as sr

from brain import Brain
import network.network_server as nsrv
from stt import recognize as stt_recognize
from audio.tts import say as tts_say
from audio.alsa_utils import silence_alsa

# Reduce ALSA/JACK noise
silence_alsa()

# ---------- HTTP + logging wiring ----------

http_app = getattr(nsrv, "app", None)


def _to_ascii(s: str) -> str:
    # Avoid latin-1 / filesystem encoding explosions in downstream loggers
    return s.encode("ascii", "replace").decode("ascii")


def serve_async(port: int) -> None:
    """Use network_server.serve_async if available, else run Flask in a thread."""
    if hasattr(nsrv, "serve_async"):
        return nsrv.serve_async(port)  # type: ignore[misc]
    if http_app is None:
        return

    def _run():
        http_app.run(host="0.0.0.0", port=port, threaded=True)

    t = threading.Thread(target=_run, name="http", daemon=True)
    t.start()


def push(msg: str, channel: str = "web") -> None:
    """
    Unified logging:
      1. Sanitize to ASCII (prevents latin-1 codec errors).
      2. Prefer nsrv.push(msg, channel) if present.
      3. Else nsrv.add_log(channel, msg).
      4. Else print.
    """
    try:
        raw = str(msg)
    except Exception:
        raw = repr(msg)
    safe = _to_ascii(raw)
    ch = channel or "web"

    # Try network_server.push
    if hasattr(nsrv, "push"):
        try:
            return nsrv.push(safe, ch)  # type: ignore[func-returns-value]
        except Exception:
            pass

    # Try network_server.add_log
    if hasattr(nsrv, "add_log"):
        try:
            return nsrv.add_log(ch, safe)  # type: ignore[func-returns-value]
        except Exception:
            pass

    # Fallback: stdout
    print(f"[{ch.upper()}] {safe}", flush=True)


# ---------- Config ----------

HTTP_PORT = int(os.environ.get("MS_HTTP_PORT", "8089"))

WAKE_WORDS = tuple(
    w.strip().lower()
    for w in os.environ.get(
        "MS_WAKE_WORDS",
        "machine spirit,hey machine spirit",
    ).split(",")
    if w.strip()
)

READY_MSG = "Machine Spirit online. Awaiting your command."
NO_MIC_MSG = "No working microphone stream available. HTTP/UI is still online."

# Stay awake this many seconds after last recognized speech
IDLE_SECONDS = float(os.environ.get("MS_IDLE_SECONDS", "10"))

brain = Brain()


# ---------- Q/A core ----------

def handle_ask(text: str) -> str:
    """Turn text into an answer, log it, speak it."""
    user = (text or "").strip()
    if not user:
        reply = "I'm listening."
    else:
        reply = brain.answer(user)

    push(reply, "web")
    try:
        tts_say(reply)
    except Exception:
        # TTS failure is non-fatal
        pass
    return reply


if http_app is not None:
    http_app.config["ON_ASK"] = handle_ask  # type: ignore[assignment]


# ---------- Helpers ----------

def _list_mics():
    try:
        return sr.Microphone.list_microphone_names()
    except Exception as e:
        push(f"Could not list microphones: {e}", "voice")
        return []


def _choose_mic_index() -> int | None:
    names = _list_mics()
    if not names:
        return None

    forced = os.environ.get("MS_MIC_INDEX", "").strip()
    if forced.isdigit():
        idx = int(forced)
        if 0 <= idx < len(names):
            push(f"Using forced mic index {idx}: {names[idx]}", "voice")
            return idx

    pref = os.environ.get("MS_MIC_NAME", "").strip().lower()
    if pref:
        for i, n in enumerate(names):
            if pref in n.lower():
                push(f"Using preferred mic {i}: {n}", "voice")
                return i

    for i, n in enumerate(names):
        low = n.lower()
        if any(k in low for k in ("usb", "mic", "microphone", "anker")) and "monitor" not in low:
            push(f"Auto-selected mic {i}: {n}", "voice")
            return i

    push(f"Falling back to first mic: {names[0]}", "voice")
    return 0


def _is_wake(text: str) -> bool:
    lt = text.lower()
    return any(w in lt for w in WAKE_WORDS)


def _strip_wake(text: str) -> str:
    s = text
    sl = s.lower()
    for w in WAKE_WORDS:
        if w in sl:
            idx = sl.find(w)
            if idx != -1:
                before = s[:idx]
                after = s[idx + len(w):]
                s = (before + " " + after)
                sl = s.lower()
    return s.strip(" ,.!?").strip()


# ---------- Voice loop: wake once, chain questions, 10s idle ----------

def voice_loop():
    try:
        r = sr.Recognizer()
    except Exception as e:
        push(f"SpeechRecognition init failed: {e}", "voice")
        return

    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.6
    r.phrase_threshold = 0.25

    idx = _choose_mic_index()
    if idx is None:
        push(NO_MIC_MSG, "voice")
        return

    try:
        with sr.Microphone(device_index=idx) as source:
            if not getattr(source, "stream", None):
                raise RuntimeError("Microphone stream not available from PyAudio/ALSA")

            try:
                r.adjust_for_ambient_noise(source, duration=1.0)
            except Exception as e:
                push(f"Ambient noise calibration failed (continuing): {e}", "voice")

            push("Say 'Machine Spirit' to wake me.", "voice")

            awake = False
            last_activity = 0.0  # timestamp of last recognized (non-empty) text

            while True:
                # Short timeout so we can enforce idle behavior
                try:
                    audio = r.listen(source, timeout=1, phrase_time_limit=6)
                except sr.WaitTimeoutError:
                    # No speech this second
                    if awake and (time.time() - last_activity) >= IDLE_SECONDS:
                        awake = False
                        push("Standing by.", "voice")
                    continue
                except Exception as e:
                    push(f"Listen error: {e}", "voice")
                    time.sleep(0.3)
                    continue

                # Run STT
                text = (stt_recognize(audio) or "").strip()
                if not text:
                    continue

                push(f"[HEARD] {text}", "voice")

                # If idle: look for wake word
                if not awake:
                    if _is_wake(text):
                        cleaned = _strip_wake(text)
                        awake = True
                        last_activity = time.time()
                        if cleaned:
                            # Wake word + command in one go
                            handle_ask(cleaned)
                        else:
                            push("Listening...", "voice")
                    # Ignore everything else while idle
                    continue

                # Already awake: no wake word required
                cleaned = _strip_wake(text).strip()
                if not cleaned:
                    # If it's basically just repeating the wake word, just refresh timer
                    last_activity = time.time()
                    continue

                # Treat every utterance as a full question/command
                last_activity = time.time()
                handle_ask(cleaned)

                # Stay awake; if nothing else comes in IDLE_SECONDS,
                # the timeout branch above drops us back to idle.

    except Exception as e:
        push(f"Fatal microphone error: {e}", "voice")
        push(NO_MIC_MSG, "voice")


# ---------- Entrypoint ----------

def main():
    push(READY_MSG, "web")
    serve_async(HTTP_PORT)

    t = threading.Thread(target=voice_loop, name="voice-loop", daemon=True)
    t.start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        push("[SHUTDOWN] Machine Spirit standing down.", "web")


if __name__ == "__main__":
    main()
