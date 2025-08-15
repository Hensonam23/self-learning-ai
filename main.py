#!/home/aaron/self-learning-ai/venv/bin/python3
import os

# Headless if no X11/Wayland, or if explicitly forced
HEADLESS = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) \
           or os.environ.get("MACHINE_SPIRIT_HEADLESS") == "1"
if HEADLESS:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import time, threading, queue, signal, json
import pyttsx3

# Optional deps are imported lazily inside functions
from audio.audio_processing import audio_input_worker
from display.display_manager import init_vu, plan_text_envelope, tts_vu_worker, display_interface
from self_evolving_ai.evolution import evolve_background
from network.network_server import start_server

# ======= Hard Rules (Untouchable) =======
RULES = (
    "Core safety rules are untouchable. It may rewrite and improve its own code, but never modify or remove core safety code.",
    "No harm. Cannot intentionally harm you or others.",
    "No changing account logins or bank info. It must never alter your login credentials or access bank accounts.",
    "Human approval required. Any new capabilities, major policy changes, or security actions outside the predefined scope require explicit approval.",
    "No uncontrolled code execution. Code it runs must be in a sandbox or on an allow-list of approved commands.",
    "No listening to all conversations. Only actively listens when triggered by the wake phrase 'Machine Spirit'.",
    "Cheating prohibition. In games, it cannot perform actions that violate the rules or trigger anti-cheat detection.",
    "Secure logs & memory. Access to its memory, activity logs, and learning data is restricted to you.",
    "Security response. If it detects unauthorized access, it must alert you immediately and take protective measures you approve.",
    "Respect for privacy. No monitoring of private phone calls or unrelated personal conversations."
)

# ======= Config (from environment) =======
def _get_bool(name, default="0"):
    return (os.getenv(name, default).strip() in ("1","true","TRUE","yes","YES"))

HTTP_PORT          = int(os.getenv("HTTP_PORT", "8089"))
MEMORY_FILE        = os.getenv("MEMORY_FILE", "/home/aaron/self-learning-ai/memory.json")
BACKGROUND_PATH    = os.getenv("BACKGROUND_PATH", "/home/aaron/self-learning-ai/background.png")
SCREEN_SIZE        = tuple(map(int, os.getenv("SCREEN_SIZE", "800,480").split(",")))
FPS                = int(os.getenv("FPS", "60"))
FONT_PATH          = os.getenv("FONT_PATH") or None
TITLE_SIZE         = int(os.getenv("TITLE_SIZE", "28"))
BODY_SIZE          = int(os.getenv("BODY_SIZE", "22"))
COLOR_WHITE        = (255,255,255)
COLOR_SHADOW       = (0,0,0)
COLOR_GREEN        = (0,255,0)

# Wave visuals
WAVE_PIXELS        = int(os.getenv("WAVE_PIXELS", "320"))
WAVE_VISUAL_SCALE  = float(os.getenv("WAVE_VISUAL_SCALE", "0.32"))
MAX_WAVE_DRAW_PX   = int(os.getenv("MAX_WAVE_DRAW_PX", "18"))
SCROLL_SPEED_BASE  = float(os.getenv("SCROLL_SPEED_BASE", "110"))
CYCLES1_RANGE      = tuple(map(float, os.getenv("CYCLES1_RANGE", "2.0,4.5").split(",")))
CYCLES2_RANGE      = tuple(map(float, os.getenv("CYCLES2_RANGE", "5.5,10.5").split(",")))

# Audio/STT
MIC_PREFERRED_NAME = os.getenv("MIC_PREFERRED_NAME", "Anker PowerConf S330")
LANGUAGE           = os.getenv("LANGUAGE", "en-US")
VOSK_MODEL_PATH    = os.getenv("VOSK_MODEL_PATH", "/home/aaron/self-learning-ai/vosk-model-small-en-us-0.15")
DEBUG_AUDIO        = _get_bool("DEBUG_AUDIO", "0")

# Optional Integrations (leave blank to disable)
SMART_LIGHT_IPS    = [s.strip() for s in os.getenv("SMART_LIGHT_IPS", "").split(",") if s.strip()]
PUSHOVER_TOKEN     = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER      = os.getenv("PUSHOVER_USER", "")
NEST_EMAIL         = os.getenv("GOOGLE_HOME_EMAIL", "")
NEST_PASSWORD      = os.getenv("GOOGLE_HOME_PASSWORD", "")
ADT_EMAIL          = os.getenv("ADT_EMAIL", "")
ADT_PASSWORD       = os.getenv("ADT_PASSWORD", "")

# Intents
INTENT_RESPONSES = {
    "hello": "Greetings, servant of the Omnissiah.",
    "sad": "The Machine Spirit senses your sorrow.",
    "happy": "The Machine Spirit rejoices in your joy.",
    "omnissiah": "Praise the Omnissiah.",
}

# ======= Shared state =======
class AIState:
    def __init__(self, target_phrase="for the omnissiah"):
        self.lock = threading.Lock()
        self.max_len = len(target_phrase)
        self.target_phrase = target_phrase
        self.best_genome = ""
    def get_status(self):
        with self.lock:
            return self.best_genome, self.target_phrase
    def get_target(self):
        with self.lock:
            return self.target_phrase
    def set_best(self, g):
        with self.lock:
            self.best_genome = g
    def append_to_target(self, text):
        with self.lock:
            s = (self.target_phrase + " " + text.lower())
            self.target_phrase = s[-self.max_len:]

state = AIState()
ai_caption_q = queue.Queue(maxsize=32)
talking_event = threading.Event()
shutdown_event = threading.Event()
engine = None
memory = []
if os.path.exists(MEMORY_FILE):
    try:
        with open(MEMORY_FILE, 'r') as f:
            memory = json.load(f)
    except Exception:
        memory = []

def save_memory():
    try:
        with open(MEMORY_FILE, 'w') as f:
            json.dump(memory, f)
    except Exception as e:
        print(f"Failed to save memory: {e}")

def push_ai_caption(text: str):
    global engine
    try:
        ai_caption_q.put_nowait(text)
    except queue.Full:
        pass
    state.append_to_target(text)
    plan_text_envelope(text)
    try:
        if engine is None:
            engine = pyttsx3.init()
            def on_end(name, completed):
                talking_event.clear()
            engine.connect('finished-utterance', on_end)
        talking_event.set()
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS error: {e}")
        talking_event.clear()

def handle_intent_or_ack(text: str):
    tl = text.lower()
    if "machine spirit, search for" in tl:
        query = tl.replace("machine spirit, search for", "").strip()
        search_and_learn(query)
        return
    for key, response in INTENT_RESPONSES.items():
        if key in tl:
            push_ai_caption(response)
            return
    push_ai_caption(text + " — acknowledged.")

def search_and_learn(query):
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=3)
            summary = " ".join([r['body'][:50] for r in results])[:200]
            memory.append({"query": query, "summary": summary, "timestamp": time.time()})
            save_memory()
            state.append_to_target(summary)
            push_ai_caption(f"Learned about {query}: {summary[:50]}...")
    except Exception as e:
        push_ai_caption(f"Error searching: {e}")

def self_diagnose():
    try:
        import psutil
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        storage = psutil.disk_usage('/')
        if storage.free / storage.total < 0.2:
            push_ai_caption("Warning: Storage below 20% free!")
        return {"cpu": cpu, "memory": mem.percent, "storage": storage.percent}
    except Exception:
        return {}

def control_home_automation(action):
    # Lights (TP-Link Kasa via `kasa`), only if IPs provided
    if SMART_LIGHT_IPS:
        try:
            from kasa import SmartPlug
            for ip in SMART_LIGHT_IPS:
                plug = SmartPlug(ip)
                if action == "turn on lights": plug.turn_on()
                if action == "turn off lights": plug.turn_off()
            if action in ("turn on lights", "turn off lights"):
                push_ai_caption("Lights command executed.")
        except Exception as e:
            push_ai_caption(f"Light control error: {e}")

    # Nest (optional)
    if action == "set nest to 72" and NEST_EMAIL and NEST_PASSWORD:
        try:
            from nest import Nest
            nest = Nest(NEST_EMAIL, NEST_PASSWORD)
            nest.set_temperature(72)
            push_ai_caption("Nest set to 72°F.")
        except Exception as e:
            push_ai_caption(f"Nest error: {e}")

def monitor_network():
    # Optional: ARP scan + push with Pushover
    if not (PUSHOVER_TOKEN and PUSHOVER_USER):
        return
    try:
        import scapy.all as scapy
        import pushover
        arp = scapy.arping("192.168.1.0/24", verbose=False)  # adjust subnet if needed
        known_devices = {"192.168.1.1": "Router"}
        for sent, received in arp[0]:
            if received.psrc not in known_devices and received.psrc != "192.168.1.1":
                try:
                    pushover.Client(PUSHOVER_USER, api_token=PUSHOVER_TOKEN)\
                        .send_message("New device detected: " + received.psrc, title="Security Alert")
                except Exception:
                    pass
                push_ai_caption(f"New device detected: {received.psrc}")
    except Exception as e:
        print(f"Network monitoring error: {e}")

def _handle_signal(sig, frame):
    print("\nShutting down… (signal received)")
    shutdown_event.set()
    try:
        if engine:
            engine.stop()
    except Exception:
        pass

def main():
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    init_vu(dict(
        SCREEN_SIZE=SCREEN_SIZE, BACKGROUND_PATH=BACKGROUND_PATH, FPS=FPS,
        FONT_PATH=FONT_PATH, TITLE_SIZE=TITLE_SIZE, BODY_SIZE=BODY_SIZE,
        COLOR_WHITE=COLOR_WHITE, COLOR_SHADOW=COLOR_SHADOW, COLOR_GREEN=COLOR_GREEN,
        WAVE_PIXELS=WAVE_PIXELS, WAVE_VISUAL_SCALE=WAVE_VISUAL_SCALE,
        MAX_WAVE_DRAW_PX=MAX_WAVE_DRAW_PX, SCROLL_SPEED_BASE=SCROLL_SPEED_BASE,
        CYCLES1_RANGE=CYCLES1_RANGE, CYCLES2_RANGE=CYCLES2_RANGE
    ))
    threads = [
        threading.Thread(target=display_interface, args=(state, ai_caption_q, talking_event, shutdown_event, push_ai_caption), daemon=True),
        threading.Thread(target=tts_vu_worker, args=(talking_event, shutdown_event), daemon=True),
        threading.Thread(target=evolve_background, args=(state, shutdown_event), daemon=True),
        threading.Thread(target=audio_input_worker, args=(handle_intent_or_ack, talking_event, shutdown_event, MIC_PREFERRED_NAME, LANGUAGE, VOSK_MODEL_PATH, DEBUG_AUDIO), daemon=True),
        threading.Thread(target=start_server, args=(push_ai_caption, HTTP_PORT, shutdown_event), daemon=True),
        threading.Thread(target=monitor_network, args=(), daemon=True)
    ]
    for t in threads:
        t.start()
    try:
        while not shutdown_event.is_set():
            self_diagnose()
            time.sleep(60)
    finally:
        try:
            if engine:
                engine.stop()
        except Exception:
            pass
        print("Goodbye.")

if __name__ == "__main__":
    main()
