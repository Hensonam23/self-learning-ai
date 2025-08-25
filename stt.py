# stt.py — multi-backend STT: Google (default), Vosk (offline), Whisper API
from __future__ import annotations
import os, io, json, shutil
import speech_recognition as sr

try:
    import requests
except Exception:
    requests = None

def has_vosk():
    try:
        import vosk  # noqa: F401
        return True
    except Exception:
        return False

def stt_google(audio: sr.AudioData) -> str:
    r = sr.Recognizer()
    try:
        return r.recognize_google(audio, show_all=False)
    except sr.UnknownValueError:
        return ""
    except Exception:
        return ""

def stt_vosk(audio: sr.AudioData) -> str:
    if not has_vosk():
        return ""
    import vosk
    model_dir = os.environ.get("VOSK_MODEL", "")
    if not model_dir or not os.path.isdir(model_dir):
        return ""
    pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
    rec = vosk.KaldiRecognizer(vosk.Model(model_dir), 16000)
    rec.AcceptWaveform(pcm)
    try:
        j = json.loads(rec.Result() or "{}")
    except Exception:
        j = {}
    return (j.get("text") or "").strip()

def stt_whisper(audio: sr.AudioData) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not (key and requests):
        return ""
    wav = audio.get_wav_data()
    files = {"file": ("speech.wav", wav, "audio/wav")}
    data = {"model": os.environ.get("OPENAI_WHISPER_MODEL", "whisper-1")}
    try:
        r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {key}"}, files=files, data=data, timeout=60)
        r.raise_for_status()
        j = r.json()
        return (j.get("text") or "").strip()
    except Exception:
        return ""

def recognize(audio: sr.AudioData) -> str:
    mode = os.environ.get("MS_STT", "auto").lower()
    order = []
    if mode == "google": order = [stt_google, stt_vosk, stt_whisper]
    elif mode == "vosk": order = [stt_vosk, stt_google, stt_whisper]
    elif mode == "whisper": order = [stt_whisper, stt_google, stt_vosk]
    else: order = [stt_google, stt_vosk, stt_whisper]

    for fn in order:
        txt = fn(audio)
        if txt:
            return txt
    return ""
