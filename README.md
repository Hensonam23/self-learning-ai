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
- **Local knowledge only**: responses draw from on-device memory; no web lookups or external APIs.

---

## Quick Start

### 1) System packages (audio + build)
```bash
sudo apt update
sudo apt install -y python3-venv python3-dev portaudio19-dev libasound2-dev \
                    libffi-dev build-essential
```

### 2) Automated Code Updates

Queue code edit tasks into `storage.memory.learning_queue` and run:

```bash
python3 tools/code_updater.py
```

Each task specifies the file to change, the text to search and replace, and a commit message. The updater applies the patch, runs tests, and commits if they pass.

### 3) Generate Code Tasks From Errors

Transform logged mistakes into code-edit tasks:

```bash
python3 tools/error_task_generator.py
```

The script asks an LLM to propose patches and queues them for the updater.

### 4) Run the Full Self-Improvement Loop

Generate tasks from logged errors and immediately apply any passing patches:

```bash
python3 tools/self_improve.py
```

This simply runs `error_task_generator.py` followed by `code_updater.py`.
