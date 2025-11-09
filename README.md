# Self-Learning AI

From-scratch evolving AI with speech input + TTS and an AI-only waveform.

## Setup
- `pip install numpy pygame pyaudio pyttsx3 SpeechRecognition`
- Optional offline STT: download a Vosk model and set `VOSK_MODEL_PATH`
- Run: `python3 main.py`  (or `python3 evolve_ai.py` if you prefer the wrapper)

## HTTP Triggers
- `GET /hello`
- `GET /sad`
- `GET /say?text=Your%20message`
