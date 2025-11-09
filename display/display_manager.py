import math
import os
import queue
import random
import re
import time

import pygame
from pygame.locals import *

# ====== module-level visual state (set by init_vu) ======
cfg = {}
WAVE_PIXELS = 320
WAVE_VISUAL_SCALE = 0.32
MAX_WAVE_DRAW_PX = 18
SCROLL_SPEED_BASE = 110
CYCLES1_RANGE = (2.0, 4.5)
CYCLES2_RANGE = (5.5, 10.5)

# AI-only VU parameters that tts_vu_worker updates
vu_amp = 0.0
vu_cycles1 = 3.0
vu_cycles2 = 7.0
vu_scroll_px_s = SCROLL_SPEED_BASE
vu_noise = 0.0

# word-level “plan” for the envelope
vu_plan = queue.Queue(maxsize=256)


def init_vu(config_dict):
    global cfg, WAVE_PIXELS, WAVE_VISUAL_SCALE, MAX_WAVE_DRAW_PX
    global SCROLL_SPEED_BASE, CYCLES1_RANGE, CYCLES2_RANGE
    cfg = dict(config_dict)
    WAVE_PIXELS = cfg.get("WAVE_PIXELS", WAVE_PIXELS)
    WAVE_VISUAL_SCALE = cfg.get("WAVE_VISUAL_SCALE", WAVE_VISUAL_SCALE)
    MAX_WAVE_DRAW_PX = cfg.get("MAX_WAVE_DRAW_PX", MAX_WAVE_DRAW_PX)
    SCROLL_SPEED_BASE = cfg.get("SCROLL_SPEED_BASE", SCROLL_SPEED_BASE)
    CYCLES1_RANGE = cfg.get("CYCLES1_RANGE", CYCLES1_RANGE)
    CYCLES2_RANGE = cfg.get("CYCLES2_RANGE", CYCLES2_RANGE)


# ---------- Text → envelope planning ----------
_vowel_re = re.compile(r"[aeiouy]", re.I)
_sibilant_re = re.compile(r"[szcxj]", re.I)
_pause_re = re.compile(r"[.,;:!?]")


def _clamp(x, a, b):
    return a if x < a else b if x > b else x


def plan_text_envelope(text: str):
    tokens = re.findall(r"\w+|[^\w\s]", text) or [text]
    base_word_sec = 0.33
    try:
        while True:
            vu_plan.get_nowait()
    except queue.Empty:
        pass
    for tok in tokens:
        if _pause_re.fullmatch(tok):
            seg = {
                "duration_s": 0.12 if tok in ",;" else 0.20,
                "target_amp": 0.08,
                "texture": 0.2,
                "pause": True,
            }
            _enqueue_plan(seg)
            continue
        L = len(tok)
        vowels = len(_vowel_re.findall(tok))
        sibil = len(_sibilant_re.findall(tok))
        vowel_ratio = vowels / max(1, L)
        sibil_ratio = sibil / max(1, L)
        dur = _clamp(base_word_sec * (0.65 + 0.08 * L), 0.18, 0.55)
        amp = 0.12 + 0.55 * vowel_ratio + 0.04 * min(L, 10)
        amp *= 0.85 + 0.30 * random.random()
        amp = _clamp(amp, 0.12, 1.0)
        tex = _clamp(0.25 + 0.9 * sibil_ratio + 0.15 * random.random(), 0.0, 1.0)
        seg = {
            "duration_s": float(dur),
            "target_amp": float(amp),
            "texture": float(tex),
            "pause": False,
        }
        _enqueue_plan(seg)
    _enqueue_plan(
        {"duration_s": 0.20, "target_amp": 0.05, "texture": 0.2, "pause": True}
    )


def _enqueue_plan(seg):
    try:
        vu_plan.put_nowait(seg)
    except queue.Full:
        try:
            vu_plan.get_nowait()
            vu_plan.put_nowait(seg)
        except Exception:
            pass


def tts_vu_worker(talking_event, shutdown_event):
    """Turn planned segments into smooth parameters while TTS is speaking."""
    global vu_amp, vu_cycles1, vu_cycles2, vu_scroll_px_s, vu_noise
    current = None
    seg_start = 0.0
    start_amp = 0.0

    def lerp(a, b, t):
        return a + (b - a) * t

    while not shutdown_event.is_set():
        if talking_event.is_set():
            if current is None:
                try:
                    current = vu_plan.get_nowait()
                    seg_start = time.time()
                    start_amp = vu_amp
                except queue.Empty:
                    vu_noise = 0.9 * vu_noise + 0.1 * (random.random() * 2 - 1) * 0.1
                    vu_amp = float(
                        _clamp(
                            vu_amp * 0.98 + 0.02 * (0.15 + 0.05 * random.random()),
                            0.0,
                            1.0,
                        )
                    )
                    time.sleep(1 / 60)
                    continue

            dur = max(0.05, float(current.get("duration_s", 0.25)))
            t = (time.time() - seg_start) / dur
            t = 1.0 if t > 1 else (0.0 if t < 0 else t)
            t_eased = t * t * (3 - 2 * t)

            target_amp = float(current.get("target_amp", 0.3))
            vu_noise = 0.85 * vu_noise + 0.15 * (random.random() * 2 - 1) * 0.15
            noisy = _clamp(target_amp * (1.0 + 0.12 * vu_noise), 0.0, 1.0)
            vu_amp = float(_clamp(lerp(start_amp, noisy, t_eased), 0.0, 1.0))

            tex = float(current.get("texture", 0.5))
            vu_cycles1 = lerp(CYCLES1_RANGE[0], CYCLES1_RANGE[1], tex)
            vu_cycles2 = lerp(CYCLES2_RANGE[0], CYCLES2_RANGE[1], tex)
            vu_scroll_px_s = SCROLL_SPEED_BASE * (0.85 + 0.5 * tex)

            if t >= 1.0:
                current = None
        else:
            vu_amp *= 0.85
            vu_scroll_px_s = SCROLL_SPEED_BASE
            vu_cycles1, vu_cycles2 = CYCLES1_RANGE[0], CYCLES2_RANGE[0]
            try:
                while True:
                    vu_plan.get_nowait()
            except queue.Empty:
                pass

        time.sleep(1 / 60)


# ---------- Rendering ----------
def _load_fonts():
    if cfg.get("FONT_PATH") and os.path.isfile(cfg["FONT_PATH"]):
        return pygame.font.Font(cfg["FONT_PATH"], cfg["TITLE_SIZE"]), pygame.font.Font(
            cfg["FONT_PATH"], cfg["BODY_SIZE"]
        )
    else:
        return pygame.font.SysFont(
            "DejaVu Sans", cfg["TITLE_SIZE"]
        ), pygame.font.SysFont("DejaVu Sans", cfg["BODY_SIZE"])


def _shadow_text(text, font, text_color, shadow_color, shadow_offset=(2, 2)):
    base = font.render(text, True, text_color)
    shadow = font.render(text, True, shadow_color)
    surf = pygame.Surface(
        (base.get_width() + shadow_offset[0], base.get_height() + shadow_offset[1]),
        pygame.SRCALPHA,
    )
    shadow.set_alpha(51)
    surf.blit(shadow, shadow_offset)
    surf.blit(base, (0, 0))
    return surf


def display_interface(
    state, ai_caption_q, talking_event, shutdown_event, push_ai_caption, on_user_text
):
    try:
        pygame.init()
        print("Pygame initialized")
        screen = pygame.display.set_mode(cfg["SCREEN_SIZE"])
        pygame.display.set_caption("Machine Spirit Interface")
        clock = pygame.time.Clock()

        background = None
        if os.path.isfile(cfg["BACKGROUND_PATH"]):
            try:
                background = pygame.image.load(cfg["BACKGROUND_PATH"])
                background = pygame.transform.scale(background, cfg["SCREEN_SIZE"])
                print("Background loaded successfully")
            except Exception as e:
                print(f"Background load error: {e}")

        title_font, body_font = _load_fonts()
        wave_left = (cfg["SCREEN_SIZE"][0] - WAVE_PIXELS) // 2
        wave_center_y = cfg["SCREEN_SIZE"][1] // 2 + 40
        wave_height_px = 200

        try:
            push_ai_caption("By the Omnissiah, systems online.")
        except queue.Full:
            pass

        input_text = ""
        current_ai_caption = ""

        while not shutdown_event.is_set():
            now = time.time()
            for event in pygame.event.get():
                if event.type == QUIT:
                    shutdown_event.set()
                elif event.type == KEYDOWN:
                    if event.key == K_ESCAPE:
                        shutdown_event.set()
                    elif event.key == K_RETURN:
                        user_prompt = input_text.strip()
                        input_text = ""
                        if user_prompt:
                            on_user_text(user_prompt)
                    elif event.key == K_BACKSPACE:
                        input_text = input_text[:-1]
                    else:
                        input_text += event.unicode

            # Pull any new caption for UI (non-blocking)
            try:
                while True:
                    current_ai_caption = ai_caption_q.get_nowait()
            except queue.Empty:
                pass

            # Background
            if background:
                screen.blit(background, (0, 0))
            else:
                screen.fill((10, 15, 20))

            # Top: mantra (best genome + target)
            best, target = state.get_status()
            genome_surf = _shadow_text(
                f"Best: {best}", title_font, cfg["COLOR_WHITE"], cfg["COLOR_SHADOW"]
            )
            target_surf = _shadow_text(
                f"Target: {target}", body_font, cfg["COLOR_WHITE"], cfg["COLOR_SHADOW"]
            )
            screen.blit(
                genome_surf,
                genome_surf.get_rect(center=(cfg["SCREEN_SIZE"][0] // 2, 40)),
            )
            screen.blit(
                target_surf,
                target_surf.get_rect(center=(cfg["SCREEN_SIZE"][0] // 2, 70)),
            )

            # Axis line for waveform
            pygame.draw.line(
                screen,
                (40, 80, 120),
                (wave_left, wave_center_y),
                (wave_left + WAVE_PIXELS, wave_center_y),
                1,
            )

            # Full-width scrolling wave (AI talking only)
            pts = []
            shift = (now * vu_scroll_px_s) % WAVE_PIXELS
            k1 = 2.0 * math.pi * float(3.0) / float(WAVE_PIXELS)
            k2 = 2.0 * math.pi * float(7.0) / float(WAVE_PIXELS)
            base_px = vu_amp * (wave_height_px / 2.0) * WAVE_VISUAL_SCALE
            if base_px > MAX_WAVE_DRAW_PX:
                base_px = MAX_WAVE_DRAW_PX

            for xpix_rel in range(WAVE_PIXELS):
                pos = xpix_rel + shift
                w = 0.62 * abs(math.sin(k1 * pos)) + 0.38 * abs(
                    math.sin(k2 * pos + 0.7)
                )
                amp_px = base_px * float(w)
                ypix = int(wave_center_y - amp_px)
                xpix = wave_left + xpix_rel
                pts.append((xpix, ypix))

            if vu_amp > 0.001 and len(pts) >= 2 and talking_event.is_set():
                pygame.draw.aalines(screen, cfg["COLOR_GREEN"], False, pts)

            # Caption
            if current_ai_caption:
                cap_surf = _shadow_text(
                    current_ai_caption,
                    body_font,
                    cfg["COLOR_WHITE"],
                    cfg["COLOR_SHADOW"],
                )
                screen.blit(
                    cap_surf,
                    cap_surf.get_rect(center=(cfg["SCREEN_SIZE"][0] // 2, 385)),
                )

            # Input (bottom)
            if input_text:
                inp_surf = _shadow_text(
                    input_text, body_font, cfg["COLOR_WHITE"], cfg["COLOR_SHADOW"]
                )
                screen.blit(
                    inp_surf,
                    inp_surf.get_rect(
                        center=(cfg["SCREEN_SIZE"][0] // 2, cfg["SCREEN_SIZE"][1] - 50)
                    ),
                )

            pygame.display.flip()
            clock.tick(cfg["FPS"])
    finally:
        try:
            pygame.quit()
        except Exception:
            pass
