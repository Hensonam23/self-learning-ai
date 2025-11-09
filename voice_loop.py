#!/usr/bin/env python3
from __future__ import annotations
import threading
import time

WAKE_WORDS = ("machine spirit", "hey machine spirit")
IDLE_TIMEOUT = 10.0       # seconds to stay awake after last interaction
PHRASE_LIMIT = 6.0        # max seconds per utterance

# Safe imports
try:
    import speech_recognition as sr
except Exception:
    sr = None  # type: ignore

try:
    from audio.alsa_utils import silence_alsa
except Exception:
    def silence_alsa():
        return

try:
    from audio.tts import say as _tts_say
except Exception:
    def _tts_say(_text: str) -> None:
        return


def _safe_text(s: str) -> str:
    """Strip/normalize to ASCII so logging never crashes on unicode."""
    if not isinstance(s, str):
        s = str(s)
    try:
        return s.encode("ascii", "ignore").decode("ascii")
    except Exception:
        return "".join(ch if ord(ch) < 128 else "?" for ch in s)


def start_voice_thread(push_cb, shutdown_event: threading.Event, answer_fn):
    """
    Voice channel only:
      - push_cb(text): logs voice status/messages (voice log only).
      - answer_fn(text) -> reply string for voice chat.
    """

    def log(msg: str):
        safe = _safe_text(msg)
        try:
            push_cb(safe)
        except Exception:
            # Always fall back to stdout, also ascii-safe
            print(safe, flush=True)

    def tts(text: str):
        safe = _safe_text(text)
        try:
            _tts_say(safe)
        except Exception:
            # Don't let TTS issues kill the loop
            pass

    if sr is None:
        log("[VOICE] speech_recognition not installed; voice disabled.")
        t = threading.Thread(target=lambda: None, name="voice(disabled)", daemon=True)
        t.start()
        return t

    silence_alsa()

    # ---- pick microphone + show all devices ----
    try:
        names = sr.Microphone.list_microphone_names()
        if not names:
            log("[VOICE] No microphones detected; voice disabled.")
            t = threading.Thread(target=lambda: None, name="voice(nomics)", daemon=True)
            t.start()
            return t

        log("[VOICE] Available input devices:")
        for i, n in enumerate(names):
            log(f"[VOICE]   {i}: {n}")

        idx = None
        for i, n in enumerate(names):
            lower = n.lower()
            if any(k in lower for k in ("anker", "usb", "mic", "microphone")) and "monitor" not in lower:
                idx = i
                break
        if idx is None:
            idx = 0
        log(f"[VOICE] Using device_index={idx}: {names[idx]}")
    except Exception as e:
        log(f"[VOICE] Could not list microphones cleanly: {e}")
        idx = None

    def _loop():
        try:
            r = sr.Recognizer()
            r.dynamic_energy_threshold = True
            r.pause_threshold = 0.6
            r.phrase_threshold = 0.25

            mic_kwargs = {}
            if idx is not None:
                mic_kwargs["device_index"] = idx

            try:
                mic = sr.Microphone(**mic_kwargs)
            except Exception as e:
                log(f"[VOICE] Failed to open microphone: {e}. Voice disabled.")
                return

            awake = False
            awake_until = 0.0

            with mic as source:
                # Ambient noise calibration
                try:
                    r.adjust_for_ambient_noise(source, duration=1.0)
                    log("[VOICE] Ambient calibration OK.")
                except Exception as e:
                    log(f"[VOICE] Ambient calibration failed (continuing): {e}")

                log("[VOICE] Say 'Machine Spirit' to wake me.")

                while not shutdown_event.is_set():
                    # idle timeout
                    now = time.time()
                    if awake and now > awake_until:
                        awake = False
                        log("[VOICE] Standing by.")

                    # listen
                    try:
                        audio = r.listen(
                            source,
                            timeout=1.0,
                            phrase_time_limit=PHRASE_LIMIT,
                        )
                    except sr.WaitTimeoutError:
                        continue
                    except Exception as e:
                        log(f"[VOICE] Listen error: {e}")
                        time.sleep(0.5)
                        continue

                    # STT
                    text = ""
                    try:
                        text = r.recognize_google(audio, language="en-US")
                    except sr.UnknownValueError:
                        log("[VOICE] Heard audio but could not understand.")
                    except Exception as e:
                        log(f"[VOICE] STT error: {e}")

                    if not text:
                        continue

                    text = text.strip()
                    low = text.lower()
                    log(f"[VOICE] Recognized: {text}")

                    # Wake logic
                    if not awake:
                        if any(w in low for w in WAKE_WORDS):
                            awake = True
                            awake_until = time.time() + IDLE_TIMEOUT
                            log("[VOICE] Wake word detected. Listening for commands.")
                        # ignore non-wake while idle
                        continue

                    # Already awake: treat as command
                    awake_until = time.time() + IDLE_TIMEOUT
                    try:
                        reply = (answer_fn(text) or "").strip()
                    except Exception as e:
                        reply = f"Error while answering: {e}"

                    if reply:
                        safe_reply = _safe_text(reply)
                        log(f"[VOICE] Spirit: {safe_reply}")
                        tts(safe_reply)
                        # keep session alive for follow-up
                        awake_until = time.time() + IDLE_TIMEOUT

        except Exception as e:
            log(f"[VOICE] Fatal error in voice loop: {e}. Voice disabled.")

    t = threading.Thread(target=_loop, name="voice", daemon=True)
    t.start()
    return t
