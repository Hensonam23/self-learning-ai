import os
import shutil
import subprocess
import threading

# Optional ENV:
#   OUTPUT_SINK="alsa_output.usb-...monitor" or leave empty to use default sink
#   VOICE="en-us" (run `espeak-ng --voices` to list)
#   TTS_RATE="175" (words per minute)
#   FORCE_PULSE="1" (kept default)


def _pick_player():
    """Prefer paplay (Pulse). Fallback to aplay -D pulse, then pw-cat."""
    if shutil.which("paplay"):
        return "paplay"
    if shutil.which("aplay"):
        return "aplay"
    if shutil.which("pw-cat"):
        return "pw-cat"
    return None


def say(text: str):
    """Speak text via espeak-ng → Pulse, without blocking the main thread."""
    text = (text or "").strip()
    if not text:
        return

    def _worker():
        player = _pick_player()
        if not player:
            print("[TTS] No audio player found (paplay/aplay/pw-cat).", flush=True)
            return

        voice = os.environ.get("VOICE", "en-us")
        rate = os.environ.get("TTS_RATE", "175")
        sink = os.environ.get("OUTPUT_SINK", "").strip()
        force_pulse = os.environ.get("FORCE_PULSE", "1") == "1"

        espeak_cmd = ["espeak-ng", "-v", voice, "-s", rate, "--stdout", text]

        # Build player command
        if player == "paplay":
            play_cmd = ["paplay"]
            if sink:
                play_cmd += [f"--device={sink}"]
        elif player == "aplay":
            play_cmd = ["aplay"]
            # Try to route via Pulse ALSA plugin to avoid hw format mismatches
            if force_pulse:
                play_cmd += ["-D", "pulse"]
        else:  # pw-cat
            play_cmd = ["pw-cat", "-p"]

        try:
            p1 = subprocess.Popen(espeak_cmd, stdout=subprocess.PIPE)
            p2 = subprocess.Popen(
                play_cmd,
                stdin=p1.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            p1.stdout.close()
            p2.wait()
        except Exception as e:
            print(f"[TTS] error: {e}", flush=True)

    threading.Thread(target=_worker, daemon=True).start()
