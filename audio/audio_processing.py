import time
from typing import Optional

import speech_recognition as sr


def _pick_device_index(preferred: str | None) -> Optional[int]:
    """Return a device_index for Microphone or None to let SR choose."""
    try:
        import pyaudio

        pa = pyaudio.PyAudio()
        chosen = None
        n = pa.get_device_count()
        pref_lower = (preferred or "").lower()
        for i in range(n):
            info = pa.get_device_info_by_index(i)
            name = str(info.get("name", ""))
            max_in = int(info.get("maxInputChannels", 0))
            if max_in <= 0:
                continue
            if pref_lower and pref_lower in name.lower():
                chosen = i
                break
            if chosen is None:
                chosen = i  # first input device fallback
        pa.terminate()
        return chosen
    except Exception:
        return None


def audio_input_worker(
    shutdown_event,
    on_text,  # callback(str)
    mic_preferred_name: str,  # e.g. "Anker PowerConf"
    debug_audio: bool,
    language: str,
    vosk_model_path: str,
):
    r = sr.Recognizer()
    r.dynamic_energy_threshold = True

    dev_index = _pick_device_index(mic_preferred_name)
    if debug_audio:
        print(f"[AUDIO] selected device_index={dev_index}", flush=True)

    # Try Vosk (offline) first if path exists
    use_vosk = False
    if vosk_model_path:
        import os

        if os.path.isdir(vosk_model_path):
            try:
                from vosk import KaldiRecognizer, Model

                model = Model(vosk_model_path)
                use_vosk = True
                if debug_audio:
                    print("[AUDIO] Using Vosk offline model", flush=True)
            except Exception as e:
                if debug_audio:
                    print(f"[AUDIO] Vosk unavailable: {e}", flush=True)

    mic_kwargs = {}
    if dev_index is not None:
        mic_kwargs["device_index"] = dev_index

    with sr.Microphone(**mic_kwargs) as source:
        try:
            if debug_audio:
                print("[AUDIO] Calibrating for ambient noise…", flush=True)
            r.adjust_for_ambient_noise(source, duration=1.5)
        except Exception as e:
            print(f"[AUDIO] calibration error: {e}", flush=True)

        while not shutdown_event.is_set():
            try:
                if debug_audio:
                    print("[AUDIO] Listening…", flush=True)
                audio = r.listen(source, timeout=2, phrase_time_limit=6)
            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                print(f"[AUDIO] listen error: {e}", flush=True)
                time.sleep(0.3)
                continue

            # Recognize
            text = ""
            if use_vosk:
                try:
                    # Minimal Vosk pass via raw data
                    raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
                    from vosk import KaldiRecognizer

                    rec = KaldiRecognizer(model, 16000)
                    if rec.AcceptWaveform(raw):
                        import json

                        res = json.loads(rec.Result() or "{}")
                        text = (res.get("text") or "").strip()
                    else:
                        res = rec.PartialResult()
                        # fall back to Google if partial only
                        text = ""
                except Exception as e:
                    if debug_audio:
                        print(f"[AUDIO] Vosk error: {e}", flush=True)

            if not text:
                try:
                    text = r.recognize_google(audio, language=language).strip()
                except sr.UnknownValueError:
                    text = ""
                except Exception as e:
                    if debug_audio:
                        print(f"[AUDIO] Google STT error: {e}", flush=True)
                    text = ""

            if text:
                print(f"Recognized: {text}", flush=True)
                try:
                    on_text(text)
                except Exception as e:
                    print(f"[AUDIO] callback error: {e}", flush=True)
            else:
                if debug_audio:
                    print("[AUDIO] (no speech)", flush=True)
