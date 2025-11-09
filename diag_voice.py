import os, sys, ctypes
import speech_recognition as sr

def silence_alsa():
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
        names = sr.Microphone.list_microphone_names()
    except Exception as e:
        print(f"[DIAG] Could not list mics: {e}")
        return []
    for i, n in enumerate(names):
        print(f"[DIAG] Mic {i}: {n}")
    return names

def pick_kwargs():
    names = sr.Microphone.list_microphone_names()
    name = os.environ.get("MS_MIC_NAME","").strip()
    idx  = os.environ.get("MS_MIC_INDEX","").strip()
    if idx.isdigit():
        i = int(idx); print(f"[DIAG] Forcing index {i}: {names[i] if i<len(names) else 'unknown'}")
        return {"device_index": i}
    if name:
        for i,n in enumerate(names):
            if name.lower() in n.lower():
                print(f"[DIAG] Forcing by name -> index {i}: {n}")
                return {"device_index": i}
    # prefer USB/hw devices
    for i,n in enumerate(names):
        if any(k in n.lower() for k in ("anker","usb","hw:","mic","microphone")) and "monitor" not in n.lower():
            print(f"[DIAG] Auto-picking hardware index {i}: {n}")
            return {"device_index": i}
    # fallback: pulse
    for i,n in enumerate(names):
        if "pulse" in n.lower():
            print(f"[DIAG] Fallback to pulse index {i}: {n}")
            return {"device_index": i}
    return {}

def stt_vosk(audio, r):
    try:
        import vosk, json
    except Exception as e:
        print(f"[DIAG] Vosk not available: {e}")
        return ""
    model_dir = os.environ.get("VOSK_MODEL","")
    if not (model_dir and os.path.isdir(model_dir)):
        print("[DIAG] VOSK_MODEL not set to a directory")
        return ""
    pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
    rec = vosk.KaldiRecognizer(vosk.Model(model_dir), 16000)
    rec.AcceptWaveform(pcm)
    try:
        j = json.loads(rec.FinalResult() or "{}")
        return (j.get("text") or "").strip()
    except Exception as e:
        print(f"[DIAG] Vosk parse error: {e}")
        return ""

def main():
    list_mics()
    r = sr.Recognizer()
    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.6
    r.phrase_threshold = 0.25
    kw = pick_kwargs()
    try:
        with sr.Microphone(sample_rate=16000, chunk_size=1024, **kw) as source:
            print("[DIAG] Mic opened. Speak for ~4 seconds...")
            try:
                r.adjust_for_ambient_noise(source, duration=0.8)
            except Exception:
                pass
            audio = r.listen(source, phrase_time_limit=4)
    except Exception as e:
        print(f"[DIAG] Could not open mic: {e}")
        sys.exit(1)

    txt = stt_vosk(audio, r)
    if txt:
        print(f"[DIAG] Recognized: {txt}")
        sys.exit(0)
    else:
        print("[DIAG] Got audio but transcription is empty.")
        sys.exit(2)

if __name__ == "__main__":
    main()
