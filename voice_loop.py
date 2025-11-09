# voice_loop.py
from __future__ import annotations
import time, threading

from audio.alsa_utils import silence_alsa

WAKE_WORDS = ("machine spirit", "hey machine spirit")
TAIL_SECONDS = 3.0
IDLE_SECONDS = 20.0

def start_voice_thread(push_cb, shutdown_event: threading.Event, answer_fn):
    silence_alsa()
    try:
        import speech_recognition as sr
        print("[VOICE] SpeechRecognition available.")
    except Exception:
        print("[VOICE] SpeechRecognition NOT available; voice disabled. (pip install SpeechRecognition pyaudio)")
        def _noop():
            while not shutdown_event.is_set():
                time.sleep(0.5)
        t = threading.Thread(target=_noop, name="voice(noop)", daemon=True)
        t.start()
        return t

    r = sr.Recognizer()
    r.pause_threshold = 0.8
    r.dynamic_energy_threshold = True

    def _loop():
        awake_until = 0.0
        last_phrase = 0.0
        buf = ""

        try:
            mic = sr.Microphone()
        except Exception as e:
            print(f"[VOICE] No microphone: {e}. Voice disabled.")
            return

        with mic as source:
            try:
                r.adjust_for_ambient_noise(source, duration=0.5)
            except Exception:
                pass
            print("[VOICE] Ready. Say 'Machine Spirit' to wake me.")

            while not shutdown_event.is_set():
                try:
                    audio = r.listen(source, timeout=1, phrase_time_limit=6)
                except sr.WaitTimeoutError:
                    if buf and (time.time() - last_phrase) >= TAIL_SECONDS:
                        try:
                            reply = answer_fn(buf)
                        except Exception as e:
                            reply = f"Error while answering: {e}"
                        push_cb(reply, "voice")
                        buf = ""
                    if time.time() > awake_until:
                        awake_until = 0.0
                    continue
                except Exception:
                    continue

                text = ""
                for recog in ("google", "sphinx"):
                    try:
                        if recog == "google":
                            text = r.recognize_google(audio)
                        else:
                            text = r.recognize_sphinx(audio)
                        break
                    except Exception:
                        text = ""
                if not text:
                    continue

                low = text.lower().strip()

                if not awake_until and any(w in low for w in WAKE_WORDS):
                    awake_until = time.time() + IDLE_SECONDS
                    push_cb("Listening.", "voice")
                    print("[VOICE] Wake word detected.")
                    continue

                if awake_until:
                    buf = (buf + " " + text).strip() if buf else text
                    last_phrase = time.time()
                    awake_until = time.time() + IDLE_SECONDS
                    print(f"[VOICE] Heard: {text}")
                    # tail check happens on timeout path

    t = threading.Thread(target=_loop, name="voice", daemon=True)
    t.start()
    return t
