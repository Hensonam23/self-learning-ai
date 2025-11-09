import os, threading, time, ctypes
import speech_recognition as sr

from brain import Brain
from network.network_server import app as http_app, serve_async, push

HTTP_PORT = int(os.environ.get("MS_HTTP_PORT", "8089"))
STT_ENGINE = os.environ.get("MS_STT", "vosk").lower()
VOSK_MODEL = os.environ.get("VOSK_MODEL", "").strip()
MIC_NAME   = os.environ.get("MS_MIC_NAME", "").strip()
MIC_INDEX  = os.environ.get("MS_MIC_INDEX", "").strip()

MSG_LISTENING = "Listening..."
MSG_MIC_ERR   = "Mic error - check cable or set MS_MIC_INDEX."

def silence_alsa():
    """Hide harmless ALSA/JACK spam in stderr."""
    try:
        ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(
            None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
        )
        def py_error_handler(filename, line, function, err, fmt):  # noqa: ARG001
            return
        c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
        asound = ctypes.cdll.LoadLibrary("libasound.so")
        asound.snd_lib_error_set_handler(c_error_handler)
    except Exception:
        pass

silence_alsa()

def list_mics():
    try:
        return sr.Microphone.list_microphone_names()
    except Exception as e:
        push(f"[VOICE] Could not list microphones: {e}")
        return []

def prefer_hardware_index():
    """Pick the *USB/hardware* mic first; avoid 'pulse' unless forced."""
    names = list_mics()
    if MIC_INDEX.isdigit():
        i = int(MIC_INDEX)
        if 0 <= i < len(names):
            push(f"[VOICE] Using forced index {i}: {names[i]}")
            return i

    # Explicit name match
    if MIC_NAME:
        for i, n in enumerate(names):
            if MIC_NAME.lower() in n.lower():
                push(f"[VOICE] Using name -> index {i}: {n}")
                return i

    # Strong hints of a real USB / hw device
    for i, n in enumerate(names):
        lower = n.lower()
        if any(k in lower for k in ("anker", "usb", "hw:", "mic", "microphone")) and "monitor" not in lower:
            push(f"[VOICE] Auto-picked hardware index {i}: {n}")
            return i

    # Fall back to 'pulse' if nothing else
    for i, n in enumerate(names):
        if "pulse" in n.lower():
            push(f"[VOICE] Falling back to pulse index {i}: {n}")
            return i

    # Final fallback
    if names:
        push(f"[VOICE] Using first mic: {names[0]}")
        return 0
    return None

def stt_with_vosk(audio, r: sr.Recognizer):
    if not VOSK_MODEL:
        return ""
    try:
        import vosk, json
        pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
        model = vosk.Model(VOSK_MODEL)
        rec = vosk.KaldiRecognizer(model, 16000)
        rec.AcceptWaveform(pcm)
        # IMPORTANT: FinalResult() returns the completed transcript for a single chunk
        j = json.loads(rec.FinalResult() or "{}")
        return (j.get("text") or "").strip()
    except Exception as e:
        push(f"[VOICE] Vosk failed: {e}")
        return ""

def stt_with_google(audio, r: sr.Recognizer):
    try:
        return r.recognize_google(audio)
    except Exception as e:
        push(f"[VOICE] Google STT failed: {e}")
        return ""

def transcribe(audio, r: sr.Recognizer):
    if STT_ENGINE in ("vosk", "auto"):
        t = stt_with_vosk(audio, r)
        if t:
            return t
    if STT_ENGINE in ("google", "auto"):
        t = stt_with_google(audio, r)
        if t:
            return t
    return ""

brain = Brain()

def handle_ask(text: str) -> str:
    reply = brain.answer(text)      # only answer; no acknowledgement fluff
    push(reply)                     # show the assistant's reply in UI
    return reply

http_app.config["ON_ASK"] = handle_ask

def voice_loop():
    r = sr.Recognizer()
    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.6
    r.phrase_threshold = 0.25

    idx = prefer_hardware_index()
    if idx is None:
        push(MSG_MIC_ERR)
        return

    try:
        with sr.Microphone(device_index=idx, sample_rate=16000, chunk_size=1024) as source:
            push(MSG_LISTENING)
            try:
                r.adjust_for_ambient_noise(source, duration=0.8)
            except Exception:
                pass
            while True:
                try:
                    audio = r.listen(source, phrase_time_limit=6)
                except Exception as e:
                    push(f"[VOICE] Listen error: {e}")
                    time.sleep(0.5)
                    continue
                text = transcribe(audio, r)
                if not text:
                    continue
                reply = brain.answer(text)
                push(reply)
    except Exception as e:
        push(MSG_MIC_ERR)
        push(f"[VOICE] Could not open microphone: {e}")

def main():
    push("Ready.")
    serve_async(HTTP_PORT)          # HTTP server is alive regardless of mic status
    push("[BOOT] voice_loop()")
    t = threading.Thread(target=voice_loop, daemon=True)
    t.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
