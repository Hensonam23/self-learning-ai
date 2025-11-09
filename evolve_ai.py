#!/usr/bin/env python3
from __future__ import annotations
import os, time, threading

from network.network_server import app as http_app, serve_async, push_web, push_voice
from brain import Brain  # local-only brain
from audio.alsa_utils import silence_alsa

HTTP_PORT = int(os.environ.get("MS_HTTP_PORT", "8089"))
STT_MODE  = os.environ.get("MS_STT", "vosk").lower()
VOSK_DIR  = os.path.expanduser(os.environ.get("VOSK_MODEL", "").strip())
MIC_NAME  = os.environ.get("MS_MIC_NAME", "").strip()
MIC_INDEX = os.environ.get("MS_MIC_INDEX", "").strip()

# Separate conversations & memory files
brain_web   = Brain(mem_path="~/self-learning-ai/data/chat_web.json")
brain_voice = Brain(mem_path="~/self-learning-ai/data/chat_voice.json")

def on_web_ask(text: str) -> str:
    return brain_web.answer(text)

http_app.config["ON_ASK"] = on_web_ask

def voice_loop():
    silence_alsa()
    try:
        import speech_recognition as sr
    except Exception as e:
        push_voice(f"[VOICE] SpeechRecognition not available: {e}")
        return

    r = sr.Recognizer()
    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.6
    r.phrase_threshold = 0.25

    def list_mics():
        try: return sr.Microphone.list_microphone_names()
        except Exception: return []

    def pick_index():
        names = list_mics()
        if MIC_INDEX.isdigit():
            i = int(MIC_INDEX)
            if 0 <= i < len(names):
                push_voice(f"[VOICE] Using forced index {i}: {names[i]}")
                return i
        if MIC_NAME:
            for i,n in enumerate(names):
                if MIC_NAME.lower() in n.lower():
                    push_voice(f"[VOICE] Using name -> index {i}: {n}")
                    return i
        for i,n in enumerate(names):
            low = n.lower()
            if any(k in low for k in ("anker","usb","hw:","mic","microphone")) and "monitor" not in low:
                push_voice(f"[VOICE] Auto-picked hardware index {i}: {n}")
                return i
        for i,n in enumerate(names):
            if "pulse" in n.lower():
                push_voice(f"[VOICE] Fallback to pulse index {i}: {n}")
                return i
        return None

    idx = pick_index()
    if idx is None:
        push_voice("[VOICE] No microphone found.")
        return

    vs_model = None
    if STT_MODE in ("vosk", "auto") and os.path.isdir(VOSK_DIR):
        try:
            from vosk import Model
            vs_model = Model(VOSK_DIR)
            push_voice("[VOICE] Vosk model loaded.")
        except Exception as e:
            push_voice(f"[VOICE] Vosk init failed, falling back: {e}")
            vs_model = None

    def stt_vosk(audio: "sr.AudioData") -> str:
        if vs_model is None:
            return ""
        try:
            from vosk import KaldiRecognizer
            import json
            pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
            rec = KaldiRecognizer(vs_model, 16000)
            if rec.AcceptWaveform(pcm):
                j = json.loads(rec.Result() or "{}")
            else:
                j = json.loads(rec.FinalResult() or "{}")
            return (j.get("text") or "").strip()
        except Exception as e:
            push_voice(f"[VOICE] Vosk error: {e}")
            return ""

    def stt_google(audio: "sr.AudioData") -> str:
        try: return r.recognize_google(audio).strip()
        except Exception: return ""

    sample_rates = (48000, 44100, 16000, None)
    push_voice("Listening.")

    while True:
        opened = False
        for sr_try in sample_rates:
            try:
                kwargs = {"device_index": idx, "chunk_size": 1024}
                if sr_try is not None:
                    kwargs["sample_rate"] = sr_try
                import speech_recognition as sr  # keep in scope
                mic = sr.Microphone(**kwargs)
            except Exception as e:
                push_voice(f"[VOICE] mic open retry ({sr_try or 'default'}): {e}")
                time.sleep(0.2)
                continue

            try:
                with mic as source:
                    try: r.adjust_for_ambient_noise(source, duration=0.8)
                    except Exception: pass
                    opened = True

                    while True:
                        try:
                            audio = r.listen(source, timeout=2, phrase_time_limit=6)
                        except sr.WaitTimeoutError:
                            continue
                        except Exception as e:
                            push_voice(f"[VOICE] Listen error: {e}")
                            break

                        text = ""
                        if vs_model is not None:
                            text = stt_vosk(audio)
                        if not text and STT_MODE in ("google","auto"):
                            text = stt_google(audio)

                        if text:
                            push_voice("> " + text)
                            try:
                                reply = brain_voice.answer(text)
                            except Exception as e:
                                reply = f"error: {e}"
                            for ln in str(reply).splitlines():
                                push_voice(ln)
            except Exception as e:
                push_voice(f"[VOICE] mic context error ({sr_try or 'default'}): {e}")

            if opened:
                break
        time.sleep(0.25)

def main():
    push_web("Ready.")
    serve_async(HTTP_PORT)          # web up regardless of audio
    push_web("[BOOT] voice_loop()")
    t = threading.Thread(target=voice_loop, daemon=True)
    t.start()
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
