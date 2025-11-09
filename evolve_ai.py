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

# Machine Spirit parameters
TARGET_PHRASE = "for the omnissiah"
GENOME_LENGTH = len(TARGET_PHRASE)
POPULATION_SIZE = 100
MUTATION_RATE = 0.05
SCREEN_SIZE = (800, 480)
BACKGROUND_PATH = "/home/aaron/self-learning-ai/background.png"
FPS = 60
FONT_PATH = None
TITLE_SIZE = 36
BODY_SIZE = 28
COLOR_WHITE = (255, 255, 255)
COLOR_SHADOW = (0, 0, 0)
COLOR_GREEN = (0, 255, 0)

# Global variables for living AI state
best_genome = ""
input_text = ""
talking = False
last_talk_time = 0.0
WAVE_PIXELS = 600
wave_envelope = deque([0.0] * WAVE_PIXELS, maxlen=WAVE_PIXELS)
ai_caption_q = queue.Queue(maxsize=32)  # UI text only
ai_speak_q = queue.Queue(maxsize=32)    # Words/utterances to drive the wave (demo)
ai_amp_q = queue.Queue(maxsize=4096)    # Real-time amplitudes
current_ai_caption = ""

# Evolutionary helpers
def create_genome():
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz ', k=GENOME_LENGTH))

def fitness(genome):
    return sum(a == b for a, b in zip(genome, TARGET_PHRASE)) / GENOME_LENGTH

def mutate(genome):
    genome_list = list(genome)
    for i in range(len(genome_list)):
        if random.random() < MUTATION_RATE:
            genome_list[i] = random.choice('abcdefghijklmnopqrstuvwxyz ')
    return ''.join(genome_list)

def crossover(parent1, parent2):
    point = random.randint(1, GENOME_LENGTH - 1)
    return parent1[:point] + parent2[point:]

def evolve_background():
    global best_genome
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
        time.sleep(5)

# AI ↔ Display integration
def push_ai_caption(text: str):
    global talking, last_talk_time
    try:
        ai_caption_q.put_nowait(text)
    except queue.Full:
        pass
    try:
        ai_speak_q.put_nowait(text)
    except queue.Full:
        pass
    talking = True
    last_talk_time = time.time()

def push_ai_amplitude(amp: float):
    amp = max(0.0, min(1.0, float(amp or 0.0)))
    try:
        ai_amp_q.put_nowait(amp)
    except queue.Full:
        pass

# DEMO ENVELOPE (word-by-word)
def word_envelope(word: str, rate_hz: int = 120):
    base = 0.09
    per_char = 0.025
    duration = min(0.45, base + per_char * len(word))
    n = max(8, int(duration * rate_hz))
    attack = max(2, int(0.25 * n))
    decay = n - attack
    t1 = np.linspace(0, 1, attack)
    t2 = np.linspace(1, 0, decay)
    env = np.concatenate([0.5 - 0.5 * np.cos(np.pi * t1), 0.5 + 0.5 * np.cos(np.pi * t2)])
    vowel_boost = 0.15 * (sum(1 for c in word.lower() if c in 'aeiouy') / max(1, len(word)))
    env = np.clip(0.25 + (0.65 + vowel_boost) * env, 0.0, 1.0)
    return env.tolist()

def demo_speaker_worker():
    gap_s = 0.06
    rate_hz = 120
    gap_samples = int(gap_s * rate_hz)
    while True:
        try:
            text = ai_speak_q.get(timeout=0.1)
        except queue.Empty:
            time.sleep(0.01)
            continue
        words = [w for w in text.split() if w]
        for w in words:
            for amp in word_envelope(w, rate_hz=rate_hz):
                push_ai_amplitude(amp)
                time.sleep(1.0 / rate_hz)
            for _ in range(gap_samples):
                push_ai_amplitude(0.0)
                time.sleep(1.0 / rate_hz)
        for amp in np.linspace(0.2, 0.0, 24):
            push_ai_amplitude(float(amp))
            time.sleep(1.0 / rate_hz)

# Text rendering helpers
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

# Display (Pygame)
def display_interface():
    global input_text, talking, last_talk_time, current_ai_caption
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    pygame.display.set_caption("Machine Spirit Interface")
    clock = pygame.time.Clock()

    # Load background image
    background = None
    if os.path.isfile(BACKGROUND_PATH):
        try:
            background = pygame.image.load(BACKGROUND_PATH)
            background = pygame.transform.scale(background, SCREEN_SIZE)
        except Exception:
            pass

    title_font, body_font = load_fonts()
    wave_left = (SCREEN_SIZE[0] - WAVE_PIXELS) // 2
    wave_center_y = SCREEN_SIZE[1] // 2 + 40  # Centered vertically
    wave_height_px = 120

    try:
        push_ai_caption("By the Omnissiah, systems online.")
    except queue.Full:
        pass

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
                        push_ai_caption(f"{user_prompt} — acknowledged.")
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

        # Top: mantra
        genome_surf = render_shadow_text(best_genome, title_font, COLOR_WHITE, COLOR_SHADOW)
        screen.blit(genome_surf, genome_surf.get_rect(center=(SCREEN_SIZE[0] // 2, 50)))

        # Axis
        pygame.draw.line(screen, (40, 80, 120), (wave_left, wave_center_y), (wave_left + WAVE_PIXELS, wave_center_y), 1)

        # Consume -> smooth -> draw
        max_per_frame = 16
        decay = 0.92
        pulled = 0
        while pulled < max_per_frame:
            try:
                wave_envelope.append(ai_amp_q.get_nowait())
                pulled += 1
            except queue.Empty:
                break
        if pulled == 0:
            wave_envelope.append(wave_envelope[-1] * decay)

        amps = np.asarray(wave_envelope, dtype=float)
        if amps.size >= 5:
            kernel = np.array([1, 2, 3, 2, 1], dtype=float)
            kernel /= kernel.sum()
            smooth = np.convolve(amps, kernel, mode='same')
        else:
            smooth = amps

        # Upsample for smoother curve
        x = np.arange(len(smooth))
        xi = np.linspace(0, len(smooth) - 1, len(smooth) * 2)
        yi = np.interp(xi, x, smooth)
        pts = []
        for i, amp in enumerate(yi):
            xpix = wave_left + int(i * (WAVE_PIXELS * 1.0 / len(yi)))
            ypix = int(wave_center_y - (amp * (wave_height_px / 2.0)))
            pts.append((xpix, ypix))

        if len(pts) >= 2:
            pygame.draw.aalines(screen, COLOR_GREEN, False, pts)

        # Caption
        if current_ai_caption:
            cap_surf = render_shadow_text(current_ai_caption, body_font, COLOR_WHITE, COLOR_SHADOW)
            screen.blit(cap_surf, cap_surf.get_rect(center=(SCREEN_SIZE[0] // 2, wave_center_y + wave_height_px // 2 + 40)))

        # Input
        if input_text:
            inp_surf = render_shadow_text(input_text, body_font, COLOR_WHITE, COLOR_SHADOW)
            screen.blit(inp_surf, inp_surf.get_rect(center=(SCREEN_SIZE[0] // 2, SCREEN_SIZE[1] - 50)))

        pygame.display.flip()
        clock.tick(FPS)

# Start background evolution thread
threading.Thread(target=evolve_background, daemon=True).start()
# Demo worker drives per-word waves
threading.Thread(target=demo_speaker_worker, daemon=True).start()

# Start resizable display and keep open with HTTP server
try:
    display_interface()
except SystemExit:
    pass
finally:
    pygame.quit()

# HTTP server (runs after display, keeps process alive)
PORT = 8089
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Machine Spirit serving at port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Machine Spirit has been silenced")
        httpd.server_close()
