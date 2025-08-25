# Machine Spirit — Local, Offline AI (Voice + Web Chat)

An on-device assistant that **thinks and learns locally**.  
Two independent experiences share the same codebase but keep **separate memory**:

- **Voice**: hot-mic loop with offline STT (Vosk).  
- **Web Chat**: terminal-style chat UI you can type into, with a scrollback log.

No OpenAI/Ollama required.

---

## Project Goals

- **Local-first**: all core behavior runs on your machine (Raspberry Pi friendly).
- **Two separate conversations**: web and voice use different memory files and do not mix.
- **Real chat log**: your typed prompts are shown (prefixed with `>`), and answers stream into the log.
- **Self-learning hooks**: queue topics and let the night learners summarize & save knowledge locally.

---

## Quick Start

### 1) System packages (audio + build)
```bash
sudo apt update
sudo apt install -y python3-venv python3-dev portaudio19-dev libasound2-dev \
                    libffi-dev build-essential
