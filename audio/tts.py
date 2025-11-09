#!/usr/bin/env python3
from __future__ import annotations
import os, shlex, subprocess, tempfile, threading, sys

# Optional override, example:
#   export TTS_CMD='espeak-ng -v en-us -s 170 %s'
# Or two-step:    'pico2wave -w %W && aplay %W'
TTS_CMD = os.environ.get("TTS_CMD", "").strip()

_engine = None
_engine_lock = threading.Lock()

def _try_pyttsx3(text: str) -> bool:
    global _engine
    try:
        import pyttsx3
        with _engine_lock:
            if _engine is None:
                _engine = pyttsx3.init()
                rate = _engine.getProperty("rate")
                _engine.setProperty("rate", int(rate * 0.95))
            _engine.say(text)
            _engine.runAndWait()
        print("[TTS] pyttsx3 ok", file=sys.stderr, flush=True)
        return True
    except Exception as e:
        print(f"[TTS] pyttsx3 fail: {e}", file=sys.stderr, flush=True)
        return False

def _try_command(text: str) -> bool:
    if not TTS_CMD:
        return False
    try:
        if "%W" in TTS_CMD:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav = tf.name
            cmd = TTS_CMD.replace("%W", shlex.quote(wav))
            subprocess.run(cmd, shell=True, check=True)
            try: os.unlink(wav)
            except Exception: pass
        else:
            cmd = TTS_CMD.replace("%s", shlex.quote(text))
            subprocess.run(cmd, shell=True, check=True)
        print("[TTS] command ok", file=sys.stderr, flush=True)
        return True
    except Exception as e:
        print(f"[TTS] command fail: {e}", file=sys.stderr, flush=True)
        return False

def _try_espeak(text: str) -> bool:
    try:
        subprocess.run(["espeak-ng", text], check=True)
        print("[TTS] espeak-ng ok", file=sys.stderr, flush=True)
        return True
    except Exception as e:
        print(f"[TTS] espeak-ng fail: {e}", file=sys.stderr, flush=True)
        return False

def _try_pico(text: str) -> bool:
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav = tf.name
        subprocess.run(["pico2wave", "-w", wav, text], check=True)
        subprocess.run(["aplay", wav], check=True)
        try: os.unlink(wav)
        except Exception: pass
        print("[TTS] pico2wave ok", file=sys.stderr, flush=True)
        return True
    except Exception as e:
        print(f"[TTS] pico2wave fail: {e}", file=sys.stderr, flush=True)
        return False

def say(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    # Prefer explicit command if provided
    if _try_command(text): return
    if _try_pyttsx3(text): return
    if _try_espeak(text): return
    _try_pico(text)
