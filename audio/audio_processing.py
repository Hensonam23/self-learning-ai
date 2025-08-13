import os, time, shutil
import numpy as np
import pyaudio
import speech_recognition as sr

def _downmix_to_mono_int16(raw_bytes, channels):
    if channels == 1:
        return raw_bytes
    data = np.frombuffer(raw_bytes, dtype=np.int16)
    try:
        data = data.reshape(-1, channels).astype(np.int32)
        mono = (data.mean(axis=1)).astype(np.int16)
        return mono.tobytes()
    except ValueError:
        return raw_bytes

def _find_input_device(p: pyaudio.PyAudio, preferred_name: str):
    print("Available audio devices:")
    pulse_index = None
    default_input_index = None
    fav_index = None
    fav_rate = None
    fav_inch = None
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        name = dev.get('name','')
        rate = int(dev.get('defaultSampleRate') or 44100)
        in_ch = int(dev.get('maxInputChannels') or 0)
        out_ch = int(dev.get('maxOutputChannels') or 0)
        print(f"Device {i}: {name} (Rate: {rate}, Input: {in_ch}, Output: {out_ch})")
        if in_ch > 0 and default_input_index is None:
            default_input_index = i
        if 'pulse' in name.lower():
            pulse_index = i
        if preferred_name.lower() in name.lower():
            fav_index, fav_rate, fav_inch = i, rate, in_ch
    if fav_index is not None:
        print(f"Selecting preferred mic: {preferred_name} at index {fav_index}")
        return fav_index, (fav_rate or 48000), max(1, fav_inch or 1)
    if pulse_index is not None:
        dev = p.get_device_info_by_index(pulse_index)
        return pulse_index, int(dev.get('defaultSampleRate') or 44100), max(1, int(dev.get('maxInputChannels') or 1))
    if default_input_index is None:
        raise RuntimeError("No input device found")
    dev = p.get_device_info_by_index(default_input_index)
    return default_input_index, int(dev.get('defaultSampleRate') or 44100), max(1, int(dev.get('maxInputChannels') or 1))

def audio_input_worker(on_recognized, talking_event, shutdown_event,
                       mic_name, language, vosk_model_path, debug=False):
    """
    Capture mic -> VAD -> STT (Vosk offline if present; else Google).
    No waveform from mic; display only uses AI TTS envelope.
    Cleanly exits when shutdown_event is set.
    """
    FORMAT = pyaudio.paInt16
    SAMPLE_WIDTH = 2
    CHUNK = 4096

    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = False
    recognizer.energy_threshold = 1e9  # do our own VAD

    use_vosk = os.path.isdir(vosk_model_path)
    have_flac = shutil.which("flac") is not None
    if use_vosk:
        print(f"Vosk model detected at {vosk_model_path}; using offline recognition.")
    elif have_flac:
        print("FLAC found; using Google speech recognition.")
    else:
        print("FLAC not found and no Vosk model; install one to enable recognition.")

    p = pyaudio.PyAudio()
    stream = None

    try:
        mic_index, rate, in_channels = _find_input_device(p, mic_name)
        channels_to_use = 1
        try:
            stream = p.open(format=FORMAT, channels=channels_to_use, rate=rate,
                            input=True, input_device_index=mic_index,
                            frames_per_buffer=CHUNK)
        except Exception as e:
            print(f"Mono open failed ({e}); retrying with channels={min(2, in_channels)}")
            channels_to_use = min(2, in_channels)
            stream = p.open(format=FORMAT, channels=channels_to_use, rate=rate,
                            input=True, input_device_index=mic_index,
                            frames_per_buffer=CHUNK)
        print(f"Audio input started (RATE={rate}, CHUNK={CHUNK}, channels={channels_to_use}, device_index={mic_index})")

        # measure noise floor (0.5s)
        baseline_frames = int(max(1, rate * 0.5 // CHUNK))
        baseline_vals = []
        for _ in range(baseline_frames):
            if shutdown_event.is_set(): break
            data = stream.read(CHUNK, exception_on_overflow=False)
            mono = _downmix_to_mono_int16(data, channels_to_use)
            a = np.frombuffer(mono, dtype=np.int16)
            baseline_vals.append(float(np.mean(np.abs(a))) / 32768.0)
        noise_floor = float(np.median(baseline_vals)) if baseline_vals else 0.005
        amp_gate = max(noise_floor * 3.0, 0.010)
        if debug:
            print(f"Noise floor: {noise_floor:.4f}  ->  speech gate: {amp_gate:.4f}")

        frames_to_seconds = CHUNK / float(rate)
        speech_frames = []
        below_gate_streak = 0

        min_phrase_seconds = 0.70
        max_phrase_seconds = 4.00
        end_silence_seconds = 0.35
        min_snr_for_recog = 1.7

        last_debug = time.time()

        while not shutdown_event.is_set():
            data = stream.read(CHUNK, exception_on_overflow=False)
            mono = _downmix_to_mono_int16(data, channels_to_use)
            a = np.frombuffer(mono, dtype=np.int16)
            amp = float(np.mean(np.abs(a))) / 32768.0

            if debug and (time.time() - last_debug > 2.0):
                print(f"amp={amp:.4f} gate={amp_gate:.4f}")
                last_debug = time.time()

            if talking_event.is_set():
                speech_frames.clear()
                below_gate_streak = 0
                time.sleep(0.01)
                continue

            if amp >= amp_gate:
                speech_frames.append(mono)
                below_gate_streak = 0
            else:
                if speech_frames:
                    below_gate_streak += 1

            phrase_secs  = len(speech_frames)   * frames_to_seconds
            silence_secs = below_gate_streak    * frames_to_seconds

            should_cut = speech_frames and (
                (phrase_secs >= min_phrase_seconds and silence_secs >= end_silence_seconds) or
                (phrase_secs >= max_phrase_seconds)
            )

            if should_cut:
                audio_segment = b"".join(speech_frames)
                speech_frames.clear()
                below_gate_streak = 0

                seg = np.frombuffer(audio_segment, dtype=np.int16)
                seg_amp = float(np.mean(np.abs(seg))) / 32768.0
                snr = (seg_amp + 1e-6) / (noise_floor + 1e-6)
                if snr < min_snr_for_recog:
                    continue

                audio = sr.AudioData(audio_segment, rate, SAMPLE_WIDTH)
                try:
                    if use_vosk and hasattr(sr.Recognizer, "recognize_vosk"):
                        text = sr.Recognizer().recognize_vosk(audio, model=vosk_model_path).lower()
                    else:
                        text = sr.Recognizer().recognize_google(audio, language=language).lower()
                    print(f"Recognized: {text}")
                    on_recognized(text)
                except sr.UnknownValueError:
                    if debug: print("Could not understand audio")
                except sr.RequestError as e:
                    print(f"Speech recognition error: {e}")
                except Exception as e:
                    print(f"Recognition pipeline error: {e}")

            time.sleep(0.005)

    except Exception as e:
        print(f"Audio input error: {e}")
    finally:
        try:
            if stream:
                try: stream.stop_stream()
                except Exception: pass
                try: stream.close()
                except Exception: pass
        finally:
            try: p.terminate()
            except Exception: pass
