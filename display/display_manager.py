# display/display_manager.py
# Minimal, robust display loop with a global queue you can push text to.

import threading
import queue
import time

try:
    import pygame
except Exception:
    pygame = None

_display_q: "queue.Queue[str]" = queue.Queue()
_stop_evt = threading.Event()

def push_caption(text: str) -> None:
    """Queue text to the on-screen terminal. Accepts plain '\n' for newlines."""
    # Make sure we don't block the caller if the queue is busy.
    try:
        _display_q.put_nowait(str(text))
    except queue.Full:
        pass

def _run_display() -> None:
    if pygame is None:
        print("[DISPLAY] pygame unavailable; screen disabled.")
        return

    pygame.init()
    try:
        screen = pygame.display.set_mode((1280, 720))
        pygame.display.set_caption("Machine Spirit")
        font = pygame.font.SysFont("Consolas,DejaVu Sans Mono,Monospace", 22)
    except Exception as e:
        print(f"[DISPLAY] failed to init: {e}")
        return

    lines: list[str] = []
    push_caption("Machine Spirit: online.")

    bg = (0, 0, 0)
    fg = (0, 240, 160)

    while not _stop_evt.is_set():
        # Drain any queued messages
        drained = False
        while True:
            try:
                msg = _display_q.get_nowait()
            except queue.Empty:
                break
            drained = True
            for part in str(msg).split("\n"):
                lines.append(part)
            lines = lines[-28:]  # keep recent lines

        # Handle window events
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                _stop_evt.set()

        # Draw
        if drained:
            screen.fill(bg)
            y = 10
            for ln in lines:
                try:
                    surf = font.render(ln, True, fg)
                except Exception:
                    # If font chokes on a glyph, fall back safely.
                    surf = font.render(ln.encode("utf-8", "ignore").decode("utf-8", "ignore"), True, fg)
                screen.blit(surf, (16, y))
                y += surf.get_height() + 4
            pygame.display.flip()

        time.sleep(0.03)  # ~33 FPS idle

    pygame.quit()

def start_display_thread() -> callable:
    """Start the screen thread and return a push function that accepts (text)."""
    t = threading.Thread(target=_run_display, daemon=True)
    t.start()
    # Return a callback the rest of the app can use.
    return push_caption

def stop_display() -> None:
    _stop_evt.set()
