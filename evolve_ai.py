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
        print(f"Caption queued: {text}")
    except queue.Full:
        print("Caption queue full")
    talking = True
    last_talk_time = time.time()
    try:
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS error: {e}")

def push_ai_amplitude(amp: float):
    amp = max(0.0, min(1.0, float(amp or 0.0)))
    try:
        ai_amp_q.put_nowait(amp)
    except queue.Full:
        pass

# Audio input thread
def audio_input_worker():
    CHUNK = 1024
    RATE = 44100
    try:
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paFloat32, channels=1, rate=RATE, input=True, frames_per_buffer=CHUNK)
        print("Audio input started successfully")
        while True:
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio_data = np.frombuffer(data, dtype=np.float32)
            amplitude = np.abs(audio_data).mean() * 10.0
            print(f"Amplitude: {amplitude}")
            push_ai_amplitude(amplitude)
    except Exception as e:
        print(f"Audio input error: {e}")
    finally:
        if 'stream' in locals():
            stream.stop_stream()
            stream.close()
        if 'p' in locals():
            p.terminate()

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

# Custom HTTP request handler
class MachineSpiritHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        print(f"Received GET request: {self.path}")
        if self.path == '/hello':
            push_ai_caption("Hello, servant of the Omnissiah.")
        elif self.path == '/sad':
            push_ai_caption("The Machine Spirit mourns your sorrow.")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 - Not Found")
            return
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Response triggered. Check the display.")

# Display (Pygame) in a separate thread
def display_interface():
    global input_text, talking, last_talk_time, current_ai_caption
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
            if pulled == 0 and not talking:
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
                ypix = int(wave_center_y - (amp * (wave_height_px / 2.0) * 10.0))
                pts.append((xpix, ypix))

            if len(pts) >= 2:
                pygame.draw.aalines(screen, COLOR_GREEN, False, pts)

            # Caption (moved up to y=385)
            if current_ai_caption:
                cap_surf = render_shadow_text(current_ai_caption, body_font, COLOR_WHITE, COLOR_SHADOW)
                screen.blit(cap_surf, cap_surf.get_rect(center=(SCREEN_SIZE[0] // 2, 385)))

            # Input
            if input_text:
                inp_surf = render_shadow_text(input_text, body_font, COLOR_WHITE, COLOR_SHADOW)
                screen.blit(inp_surf, inp_surf.get_rect(center=(SCREEN_SIZE[0] // 2, SCREEN_SIZE[1] - 50)))

            pygame.display.flip()
            clock.tick(FPS)
            if not talking and (now - last_talk_time) > 3:
                talking = False

    except Exception as e:
        print(f"Display error: {e}")
    finally:
        pygame.quit()

# Start display in a separate thread
display_thread = threading.Thread(target=display_interface, daemon=True)
display_thread.start()

# Start background evolution thread
threading.Thread(target=evolve_background, daemon=True).start()
threading.Thread(target=audio_input_worker, daemon=True).start()

# HTTP server with web control
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
