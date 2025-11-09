#!/home/aaron/self-learning-ai/venv/bin/python3
import random
import numpy as np
import http.server
import socketserver
import pygame
from pygame.locals import *
import os
import time
import threading
from collections import deque
import queue
import pyaudio
import pyttsx3
import speech_recognition as sr
import shutil  # which('flac')
import re

# =================== Config ===================
TARGET_PHRASE = "for the omnissiah"  # Initial target; will update dynamically
GENOME_LENGTH = len(TARGET_PHRASE)
POPULATION_SIZE = 100
MUTATION_RATE = 0.15  # Increased for faster adaptation
SCREEN_SIZE = (800, 480)
BACKGROUND_PATH = "/home/aaron/self-learning-ai/background.png"
FPS = 60
FONT_PATH = None
TITLE_SIZE = 20  # Reduced for better fit
BODY_SIZE = 18   # Reduced for better fit
COLOR_WHITE = (255, 255, 255)
COLOR_SHADOW = (0, 0, 0)
COLOR_GREEN = (0, 255, 0)  # AI voice waveform
# Debug logging
DEBUG = False  # Disabled to reduce clutter
# ===== Waveform visuals (shorter line, smaller waves, full-width animation) =====
WAVE_PIXELS = 340  # overall line length (slightly shorter)
WAVE_VISUAL_SCALE = 0.45  # lowers overall height (smaller waves)
MAX_WAVE_DRAW_PX = 26  # hard pixel cap for peaks
SCROLL_SPEED_BASE = 110  # base scroll speed (px/s); modulated by speech plan
# Dynamic wave “formant” ranges; we interpolate within these per word segment
CYCLES1_RANGE = (2.2, 5.0)  # broader peaks
CYCLES2_RANGE = (6.0, 11.0)  # fine ripple overlay
# Audio / STT
MIC_PREFERRED_NAME = "Anker PowerConf S330"
LANGUAGE = "en-US"
VOSK_MODEL_PATH = "/home/aaron/self-learning-ai/vosk-model-small-en-us-0.15"  # optional offline model
# What to say when we hear certain words/phrases
INTENT_RESPONSES = {
    "hello": "Greetings, servant of the Omnissiah.",
    "sad": "The Machine Spirit senses your sorrow.",
    "happy": "The Machine Spirit rejoices in your joy.",
    "stop listening": "As you command.",
    "omnissiah": "Praise the Omnissiah.",
}
LOG_FILE = "/home/aaron/self-learning-ai/interactions.log"
MAX_LOG_ENTRIES = 10  # Limit log to last 10 interactions
MAX_DISPLAY_LINES = 3  # Limit to last 3 lines of text

# =================== Globals ===================
best_genome = ""
input_text = ""
talking = False
last_talk_time = 0.0
# AI caption + TTS
ai_caption_q = queue.Queue(maxsize=32)
current_ai_caption = ""
engine = None  # pyttsx3 engine
# ===== AI VU State (drives the wave) =====
vu_amp = 0.0  # 0..1 overall amplitude
vu_cycles1 = 3.0  # large undulation count across the line
vu_cycles2 = 7.0  # fine ripple
vu_scroll_px_s = SCROLL_SPEED_BASE  # scroll speed
vu_noise = 0.0  # small jitter
# A queue of “segments” planned from the spoken text; tts_vu_worker consumes this.
vu_plan = queue.Queue(maxsize=256)

# =================== Evolution ===================
def create_genome():
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz .,!?:;-', k=GENOME_LENGTH))  # Added punctuation

def fitness(genome):
    return sum(a == b for a, b in zip(genome, TARGET_PHRASE)) / GENOME_LENGTH

def mutate(genome):
    genome_list = list(genome)
    for i in range(len(genome_list)):
        if random.random() < MUTATION_RATE:
            genome_list[i] = random.choice('abcdefghijklmnopqrstuvwxyz .,!?:;-')  # Added punctuation
    return ''.join(genome_list)

def crossover(parent1, parent2):
    if GENOME_LENGTH <= 1:
        return parent1
    point = random.randint(1, GENOME_LENGTH - 1)
    return parent1[:point] + parent2[point:]

def evolve_background():
    global best_genome, TARGET_PHRASE, GENOME_LENGTH
    population = [create_genome() for _ in range(POPULATION_SIZE)]
    best_genome = population[0]
    while True:
        population.sort(key=fitness, reverse=True)
        if fitness(population[0]) > fitness(best_genome):
            best_genome = population[0]
        survivors = population[:POPULATION_SIZE // 4]
        offspring = []
        while len(offspring) < POPULATION_SIZE - len(survivors):
            offspring.append(mutate(crossover(*random.sample(survivors, 2))))
        population = survivors + offspring
        time.sleep(0.3)  # Faster evolution
        if DEBUG:
            print(f"Target Phrase: {TARGET_PHRASE}, Best Genome: {best_genome}, Fitness: {fitness(best_genome):.2f}")

# =================== AI ↔ Display ===================
def push_ai_caption(text: str):
    """Queue caption, speak with pyttsx3, plan a realistic VU envelope, and toggle 'talking'."""
    global talking, last_talk_time, engine, TARGET_PHRASE, GENOME_LENGTH
    try:
        ai_caption_q.put_nowait(text)
        if DEBUG:
            print(f"Caption queued: {text}")
    except queue.Full:
        print("Caption queue full")
    # Update target phrase with recognized text for self-learning
    TARGET_PHRASE += " " + text.lower()  # Append to target for evolution
    TARGET_PHRASE = TARGET_PHRASE[-GENOME_LENGTH:]  # Keep length constant
    GENOME_LENGTH = len(TARGET_PHRASE)  # Update length
    # Log the interaction
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} - Recognized: {text}\n")
    # Build a rough per-word amplitude/texture plan (consumed by tts_vu_worker)
    plan_text_envelope(text)
    last_talk_time = time.time()
    try:
        if engine is None:
            engine = pyttsx3.init()
            def on_end(name, completed):
                global talking, vu_amp
                talking = False
                vu_amp = 0.0
                # Drain any leftover plan segments
                try:
                    while True:
                        vu_plan.get_nowait()
                except queue.Empty:
                    pass
                if DEBUG:
                    print("TTS finished; listening re-enabled")
            engine.connect('finished-utterance', on_end)
        talking = True
        engine.say(text)
        engine.runAndWait()
        # Log the response
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} - Response: {text}\n")
    except Exception as e:
        print(f"TTS error: {e}")
        talking = False
        vu_amp = 0.0
        try:
            while True:
                vu_plan.get_nowait()
        except queue.Empty:
            pass

def handle_intent(text: str) -> bool:
    for key, response in INTENT_RESPONSES.items():
        if key in text:
            push_ai_caption(response)
            return True
    return False

# --------- Speech-plan synthesis (more realistic VU from text) ----------
_vowel_re = re.compile(r"[aeiouy]", re.I)
_sibilant_re = re.compile(r"[szcxj]", re.I)
_pause_re = re.compile(r"[.,;:!?:;-]")
def _clamp(x, a, b):
    return a if x < a else b if x > b else x

def plan_text_envelope(text: str):
    """Convert a sentence into a queue of amplitude/texture segments."""
    # Split into tokens but keep punctuation for pauses
    tokens = re.findall(r"\w+|[^\w\s]", text)
    if not tokens:
        tokens = [text]
    # “Speaking rate” estimate (affects durations): ~180 wpm -> ~0.33s/word baseline
    base_word_sec = 0.33
    # Clear any stale plan
    try:
        while True:
            vu_plan.get_nowait()
    except queue.Empty:
        pass
    for tok in tokens:
        if _pause_re.fullmatch(tok):
            # Short pause/low energy after punctuation
            seg = {
                "duration_s": 0.12 if tok in ",;" else 0.20,
                "target_amp": 0.08,
                "texture": 0.2,
                "pause": True,
            }
            _enqueue_plan(seg)
            continue
        # Word features
        L = len(tok)
        vowels = len(_vowel_re.findall(tok))
        sibil = len(_sibilant_re.findall(tok))
        vowel_ratio = vowels / max(1, L)
        sibil_ratio = sibil / max(1, L)
        # Duration scales with word length, with a floor/ceiling
        dur = _clamp(base_word_sec * (0.65 + 0.08 * L), 0.18, 0.55)
        # Target amplitude: more vowels -> more sonority; length bumps slightly
        amp = 0.12 + 0.55 * vowel_ratio + 0.04 * min(L, 10)
        amp *= (0.85 + 0.30 * random.random())  # human variability
        amp = _clamp(amp, 0.12, 1.0)
        # Texture 0..1: more sibilants -> finer ripple & faster scroll
        tex = _clamp(0.25 + 0.9 * sibil_ratio + 0.15 * random.random(), 0.0, 1.0)
        seg = {
            "duration_s": float(dur),
            "target_amp": float(amp),
            "texture": float(tex),
            "pause": False,
        }
        _enqueue_plan(seg)
    # Gentle tail drop to zero so the wave settles nicely
    _enqueue_plan({"duration_s": 0.20, "target_amp": 0.05, "texture": 0.2, "pause": True})

def _enqueue_plan(seg):
    try:
        vu_plan.put_nowait(seg)
    except queue.Full:
        # If plan is somehow full, drop oldest to keep things responsive
        try:
            vu_plan.get_nowait()
            vu_plan.put_nowait(seg)
        except Exception:
            pass

# =================== Audio + STT ===================
def find_input_device(p: pyaudio.PyAudio):
    """Prefer Anker mic; else Pulse; else first input device. Return (index, rate, channels)."""
    print("Available audio devices:")
    pulse_index = None
    default_input_index = None
    anker_index = None
    anker_rate = None
    anker_inch = None
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        name = dev.get('name', '') or ''
        rate = int(dev.get('defaultSampleRate') or 44100)
        in_ch = int(dev.get('maxInputChannels') or 0)
        out_ch = int(dev.get('maxOutputChannels') or 0)
        print(f"Device {i}: {name} (Rate: {rate}, Input: {in_ch}, Output: {out_ch})")
        if in_ch > 0 and default_input_index is None:
            default_input_index = i
        if 'pulse' in name.lower():
            pulse_index = i
        if MIC_PREFERRED_NAME.lower() in name.lower():
            anker_index = i
            anker_rate = rate
            anker_inch = in_ch
    if anker_index is not None:
        print(f"Selecting preferred mic: {MIC_PREFERRED_NAME} at index {anker_index}")
        return anker_index, (anker_rate or 48000), max(1, anker_inch or 1)
    if pulse_index is not None:
        dev = p.get_device_info_by_index(pulse_index)
        return pulse_index, int(dev.get('defaultSampleRate') or 44100), max(1, int(dev.get('maxInputChannels') or 1))
    if default_input_index is None:
        raise RuntimeError("No input device found")
    dev = p.get_device_info_by_index(default_input_index)
    return default_input_index, int(dev.get('defaultSampleRate') or 44100), max(1, int(dev.get('maxInputChannels') or 1))

def downmix_to_mono_int16(raw_bytes: bytes, channels: int) -> bytes:
    if channels == 1:
        return raw_bytes
    data = np.frombuffer(raw_bytes, dtype=np.int16)
    try:
        data = data.reshape(-1, channels).astype(np.int32)
        mono = (data.mean(axis=1)).astype(np.int16)
        return mono.tobytes()
    except ValueError:
        return raw_bytes

def audio_input_worker():
    """
    Capture mic in 16-bit PCM, VAD phrases, then recognize.
    Wave animation is AI-only; mic amplitudes are NOT drawn.
    """
    FORMAT = pyaudio.paInt16
    SAMPLE_WIDTH = 2
    CHUNK = 4096
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = False
    recognizer.energy_threshold = 100  # Lowered to improve detection
    use_vosk = os.path.isdir(VOSK_MODEL_PATH)
    have_flac = shutil.which("flac") is not None
    if use_vosk:
        print(f"Vosk model detected at {VOSK_MODEL_PATH}; using offline recognition.")
    elif have_flac:
        print("FLAC found; using Google speech recognition.")
    else:
        print("FLAC not found and no Vosk model; install one to enable recognition.")
    p = pyaudio.PyAudio()
    stream = None
    try:
        mic_index, rate, in_channels = find_input_device(p)
        if mic_index is None:
            raise RuntimeError("No input device found")
        channels_to_use = 1
        try:
            stream = p.open(format=FORMAT,
                            channels=channels_to_use,
                            rate=rate,
                            input=True,
                            input_device_index=mic_index,
                            frames_per_buffer=CHUNK)
        except Exception as e:
            print(f"Mono open failed ({e}); retrying with channels={min(2, in_channels)}")
            channels_to_use = min(2, in_channels)
            stream = p.open(format=FORMAT,
                            channels=channels_to_use,
                            rate=rate,
                            input=True,
                            input_device_index=mic_index,
                            frames_per_buffer=CHUNK)
        print(f"Audio input started (RATE={rate}, CHUNK={CHUNK}, channels={channels_to_use}, device_index={mic_index})")
        # ---- Measure noise floor (0.5s) and set gate ----
        baseline_frames = int(max(1, rate * 0.5 // CHUNK))
        baseline_vals = []
        for _ in range(baseline_frames):
            data = stream.read(CHUNK, exception_on_overflow=False)
            mono = downmix_to_mono_int16(data, channels_to_use)
            a = np.frombuffer(mono, dtype=np.int16)
            baseline_vals.append(float(np.mean(np.abs(a))) / 32768.0)
        noise_floor = float(np.median(baseline_vals)) if baseline_vals else 0.005
        amp_gate = max(noise_floor * 3.0, 0.010)
        if DEBUG:
            print(f"Noise floor: {noise_floor:.4f} -> speech gate: {amp_gate:.4f}")
        # ---- VAD buffers & parameters ----
        frames_to_seconds = CHUNK / float(rate)
        speech_frames = []
        below_gate_streak = 0
        min_phrase_seconds = 0.70
        max_phrase_seconds = 4.00
        end_silence_seconds = 0.35
        min_snr_for_recog = 1.7
        last_debug = time.time()
        while True:
            data = stream.read(CHUNK, exception_on_overflow=False)
            mono = downmix_to_mono_int16(data, channels_to_use)
            a = np.frombuffer(mono, dtype=np.int16)
            amp = float(np.mean(np.abs(a))) / 32768.0
            if DEBUG:
                print(f"amp={amp:.4f} gate={amp_gate:.4f} talking={talking}")
            if talking:
                speech_frames.clear()
                below_gate_streak = 0
                time.sleep(0.01)
                continue
            # ---- VAD ----
            if amp >= amp_gate:
                speech_frames.append(mono)
                below_gate_streak = 0
            else:
                if speech_frames:
                    below_gate_streak += 1
            phrase_secs = len(speech_frames) * frames_to_seconds
            silence_secs = below_gate_streak * frames_to_seconds
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
                    if DEBUG:
                        print(f"Skipping low-SNR chunk (snr={snr:.2f})")
                    continue
                audio = sr.AudioData(audio_segment, rate, SAMPLE_WIDTH)
                try:
                    if use_vosk:
                        text = sr.Recognizer().recognize_vosk(audio, model=VOSK_MODEL_PATH).lower()
                    else:
                        text = sr.Recognizer().recognize_google(audio, language=LANGUAGE).lower()
                    if DEBUG:
                        print(f"Recognized: {text}")
                    handle_intent(text)
                except sr.UnknownValueError:
                    if DEBUG:
                        print("Could not understand audio")
                except sr.RequestError as e:
                    if DEBUG:
                        print(f"Speech recognition error: {e}")
                except Exception as e:
                    if DEBUG:
                        print(f"Recognition pipeline error: {e}")
            time.sleep(0.005)
    except Exception as e:
        print(f"Audio input error: {e}")
    finally:
        try:
            if stream:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
        finally:
            try:
                p.terminate()
            except Exception:
                pass

# =================== TTS VU Animator (AI voice only) ===================
def tts_vu_worker():
    """
    Consume the planned word-level segments and convert them into a smooth,
    evolving envelope that the display renders as a full-width, scrolling wave.
    """
    global talking, vu_amp, vu_cycles1, vu_cycles2, vu_scroll_px_s, vu_noise
    current = None
    seg_start = 0.0
    start_amp = 0.0
    def lerp(a, b, t):
        return a + (b - a) * t
    while True:
        if talking:
            # Pull a segment if we don't have one
            if current is None:
                try:
                    current = vu_plan.get_nowait()
                    seg_start = time.time()
                    start_amp = vu_amp
                except queue.Empty:
                    # No new plan yet; keep gentle motion
                    _idle_motion()
                    time.sleep(1.0 / 60.0)
                    continue
            # Progress 0..1 for this segment
            dur = max(0.05, float(current.get("duration_s", 0.25)))
            t = (time.time() - seg_start) / dur
            t = 1.0 if t > 1.0 else (0.0 if t < 0.0 else t)
            # Ease-in-out for natural syllabic swell
            t_eased = t * t * (3 - 2 * t)
            # Target amplitude with tiny jitter
            target_amp = float(current.get("target_amp", 0.3))
            vu_noise = 0.85 * vu_noise + 0.15 * (random.random() * 2 - 1) * 0.15
            noisy = _clamp(target_amp * (1.0 + 0.12 * vu_noise), 0.0, 1.0)
            # Interpolate current amplitude toward target
            vu_amp = float(_clamp(lerp(start_amp, noisy, t_eased), 0.0, 1.0))
            # Texture controls spatial detail + scroll speed
            tex = float(current.get("texture", 0.5))
            vu_cycles1 = lerp(CYCLES1_RANGE[0], CYCLES1_RANGE[1], tex)
            vu_cycles2 = lerp(CYCLES2_RANGE[0], CYCLES2_RANGE[1], tex)
            vu_scroll_px_s = SCROLL_SPEED_BASE * (0.85 + 0.5 * tex)
            if t >= 1.0:
                current = None
                start_amp = vu_amp
        else:
            # Reset to idle state
            vu_amp = 0.0  # Force waveform to stop when not talking
            vu_scroll_px_s = SCROLL_SPEED_BASE
            vu_cycles1 = CYCLES1_RANGE[0]
            vu_cycles2 = CYCLES2_RANGE[0]
            # Drain plan
            try:
                while True:
                    vu_plan.get_nowait()
            except queue.Empty:
                pass
        time.sleep(1.0 / 60.0)

def _idle_motion():
    """Subtle motion if we momentarily lack segments while talking."""
    global vu_amp, vu_noise
    vu_noise = 0.0  # Reset noise to 0 when idle
    vu_amp = 0.0  # Ensure idle state has no amplitude

# =================== Text rendering ===================
def load_fonts():
    if FONT_PATH and os.path.isfile(FONT_PATH):
        return pygame.font.Font(FONT_PATH, TITLE_SIZE), pygame.font.Font(FONT_PATH, BODY_SIZE)
    else:
        return pygame.font.SysFont('DejaVu Sans', TITLE_SIZE), pygame.font.SysFont('DejaVu Sans', BODY_SIZE)

def render_shadow_text(text: str, font: pygame.font.Font, text_color, shadow_color, shadow_offset=(2, 2)):
    base = font.render(text, True, text_color)
    shadow = font.render(text, True, shadow_color)
    surf = pygame.Surface((base.get_width() + shadow_offset[0], base.get_height() + shadow_offset[1]), pygame.SRCALPHA)
    shadow.set_alpha(51)  # ~20% opacity
    surf.blit(shadow, shadow_offset)
    surf.blit(base, (0, 0))
    return surf

# =================== HTTP handler ===================
class MachineSpiritHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        print(f"Received GET request: {self.path}")
        if self.path == '/hello':
            push_ai_caption("Hello, servant of the Omnissiah.")
        elif self.path == '/sad':
            push_ai_caption("The Machine Spirit mourns your sorrow.")
        elif self.path == '/replay':
            self.replay_log()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 - Not Found")
            return
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Response triggered. Check the display.")

    def replay_log(self):
        global engine
        try:
            with open(LOG_FILE, "r") as f:
                lines = f.readlines()[-MAX_LOG_ENTRIES:]
                for line in reversed(lines):
                    if "Response: " in line:
                        response = line.split("Response: ")[1].strip()
                        if engine is None:
                            engine = pyttsx3.init()
                        engine.say(response)
                        engine.runAndWait()
                        time.sleep(0.5)  # Brief pause between replays
        except Exception as e:
            print(f"Replay error: {e}")

# =================== Display (Pygame) ===================
def display_interface():
    global input_text, talking, last_talk_time, current_ai_caption, TARGET_PHRASE
    try:
        pygame.init()
        print("Pygame initialized")
        screen = pygame.display.set_mode(SCREEN_SIZE)
        pygame.display.set_caption("Machine Spirit Interface")
        clock = pygame.time.Clock()
        # Load background image
        background = None
        if os.path.isfile(BACKGROUND_PATH):
            try:
                background = pygame.image.load(BACKGROUND_PATH)
                background = pygame.transform.scale(background, SCREEN_SIZE)
                print("Background loaded successfully")
            except Exception as e:
                print(f"Background load error: {e}")
        title_font, body_font = load_fonts()
        wave_left = (SCREEN_SIZE[0] - WAVE_PIXELS) // 2
        wave_center_y = SCREEN_SIZE[1] // 2 + 40
        wave_height_px = 200
        try:
            push_ai_caption("By the Omnissiah, systems online.")
        except queue.Full:
            pass
        phase_drift = 0.0
        while True:
            now = time.time()
            for event in pygame.event.get():
                if event.type == QUIT:
                    return
                elif event.type == KEYDOWN:
                    if event.key == K_RETURN:
                        user_prompt = input_text.strip()
                        input_text = ""
                        if user_prompt:
                            push_ai_caption(f"{user_prompt} - acknowledged.")
                    elif event.key == K_BACKSPACE:
                        input_text = input_text[:-1]
                    else:
                        input_text += event.unicode
            # Pull any new caption for the UI (non-blocking)
            try:
                while True:
                    current_ai_caption = ai_caption_q.get_nowait()
                    talking = True
                    last_talk_time = now
            except queue.Empty:
                pass
            # Background
            if background:
                screen.blit(background, (0, 0))
            else:
                screen.fill((10, 15, 20))
            # Top: mantra (evolving) and target phrase with wrapping and limited lines
            genome_text = f"Best: {best_genome}"
            target_text = f"Target: {TARGET_PHRASE}"
            genome_lines = [genome_text[i:i + 30] for i in range(0, len(genome_text), 30)]  # Wrap at 30 chars
            target_lines = [target_text[i:i + 30] for i in range(0, len(target_text), 30)]  # Wrap at 30 chars
            # Limit to last MAX_DISPLAY_LINES
            genome_lines = genome_lines[-MAX_DISPLAY_LINES:]
            target_lines = target_lines[-MAX_DISPLAY_LINES:]
            y_offset = 50
            for line in genome_lines:
                genome_surf = render_shadow_text(line, title_font, COLOR_WHITE, COLOR_SHADOW)
                screen.blit(genome_surf, genome_surf.get_rect(center=(SCREEN_SIZE[0] // 2, y_offset)))
                y_offset += 25  # Increased spacing
            y_offset = 80
            for line in target_lines:
                target_surf = render_shadow_text(line, body_font, COLOR_WHITE, COLOR_SHADOW)
                screen.blit(target_surf, target_surf.get_rect(center=(SCREEN_SIZE[0] // 2, y_offset)))
                y_offset += 25  # Increased spacing
            # Axis line for waveform
            pygame.draw.line(screen, (40, 80, 120),
                             (wave_left, wave_center_y),
                             (wave_left + WAVE_PIXELS, wave_center_y), 1)
            # -------- Full-width scrolling wave (AI talking only) --------
            pts = []
            shift = ((now * vu_scroll_px_s) % WAVE_PIXELS)
            phase_drift += 0.015
            k1 = 2.0 * np.pi * float(vu_cycles1) / float(WAVE_PIXELS)
            k2 = 2.0 * np.pi * float(vu_cycles2) / float(WAVE_PIXELS)
            base_px = vu_amp * (wave_height_px / 2.0) * WAVE_VISUAL_SCALE
            if base_px > MAX_WAVE_DRAW_PX:
                base_px = MAX_WAVE_DRAW_PX
            for xpix_rel in range(WAVE_PIXELS):
                pos = (xpix_rel + shift)
                w = 0.62 * abs(np.sin(k1 * pos + 0.4 * np.sin(phase_drift))) \
                    + 0.38 * abs(np.sin(k2 * pos * (1.0 + 0.02 * np.sin(0.6 * phase_drift)) + 0.7))
                amp_px = base_px * float(w)
                ypix = int(wave_center_y - amp_px)
                xpix = wave_left + xpix_rel
                pts.append((xpix, ypix))
            if vu_amp > 0.001 and len(pts) >= 2:
                pygame.draw.aalines(screen, COLOR_GREEN, False, pts)
            # Caption
            if current_ai_caption:
                cap_surf = render_shadow_text(current_ai_caption, body_font, COLOR_WHITE, COLOR_SHADOW)
                screen.blit(cap_surf, cap_surf.get_rect(center=(SCREEN_SIZE[0] // 2, 385)))
            # Input (bottom)
            if input_text:
                inp_surf = render_shadow_text(input_text, body_font, COLOR_WHITE, COLOR_SHADOW)
                screen.blit(inp_surf, inp_surf.get_rect(center=(SCREEN_SIZE[0] // 2, SCREEN_SIZE[1] - 50)))
            pygame.display.flip()
            clock.tick(FPS)
    except Exception as e:
        print(f"Display error: {e}")
    finally:
        pygame.quit()

# =================== Threads ===================
display_thread = threading.Thread(target=display_interface, daemon=True)
display_thread.start()
threading.Thread(target=evolve_background, daemon=True).start()
threading.Thread(target=audio_input_worker, daemon=True).start()
threading.Thread(target=tts_vu_worker, daemon=True).start()  # AI-only wave animator

# =================== HTTP server ===================
PORT = 8089
Handler = MachineSpiritHandler
try:
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Machine Spirit serving at port {PORT}")
        httpd.serve_forever()
except Exception as e:
    print(f"HTTP server error: {e}")
except KeyboardInterrupt:
    print("Machine Spirit has been silenced")
    if 'httpd' in locals():
        httpd.server_close()
finally:
    if 'display_thread' in locals() and display_thread.is_alive():
        pygame.quit()
