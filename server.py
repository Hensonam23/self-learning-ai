from flask import Flask, request, jsonify
from answer_engine import respond  # your core AI call
from memory_manager import MemoryManager

app = Flask(__name__)

# Per-channel rolling context only (Step #1 scope)
memory = MemoryManager(
    max_turns=20,
    channels=["web", "voice"]
)

# Small, lightweight system hint:
# (Just enough so it replies like a normal assistant, not a dictionary.)
SYSTEM_HINT = (
    "You are Machine Spirit, a helpful, direct assistant running on a private system. "
    "Use recent conversation context to answer like a consistent mind. "
    "Do not give dictionary-style answers or random grammar lessons unless asked."
)


def build_context_prompt(channel: str, user_message: str, max_context_chars: int = 6000) -> str:
    """
    Step #1 requirement:
    - Keep web and voice as separate sessions.
    - Each remembers its own last N turns.
    - Feed that context into answer_engine.respond(...).

    This function:
    - Pulls recent turns for the given channel.
    - Trims if needed so we don't send huge prompts.
    - Appends the new user message at the end.
    """
    history = memory.get_context(channel)

    # Format history as simple alternating lines
    blocks = [f"User: {t['user']}\nAI: {t['ai']}" for t in history]

    # Take from newest backwards within size limit
    selected = []
    total_len = 0
    for block in reversed(blocks):
        length = len(block) + 1
        if total_len + length > max_context_chars:
            break
        selected.append(block)
        total_len += length

    selected.reverse()
    history_text = "\n".join(selected).strip()

    if history_text:
        prompt = (
            f"{SYSTEM_HINT}\n"
            f"Channel: {channel}\n"
            f"Recent conversation on this channel:\n"
            f"{history_text}\n"
            f"Now continue the conversation.\n"
            f"User: {user_message}\nAI:"
        )
    else:
        prompt = (
            f"{SYSTEM_HINT}\n"
            f"Channel: {channel}\n"
            f"User: {user_message}\nAI:"
        )

    return prompt


def handle_message(channel: str, user_message: str) -> str:
    """
    Shared handler:
    - Build context-aware prompt for this channel.
    - Call answer_engine.respond(...)
    - Store the new turn in that channel's memory.
    """
    prompt = build_context_prompt(channel, user_message)
    ai_response = respond(prompt)
    memory.add_turn(channel, user_message, ai_response)
    return ai_response


@app.post("/api/chat")
def web_chat():
    """
    Web chat endpoint.
    Expects JSON: { "message": "<user text>" }
    Uses 'web' channel context only.
    """
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Missing 'message'"}), 400

    reply = handle_message("web", user_message)
    return jsonify({"reply": reply})


@app.post("/api/voice")
def voice_chat():
    """
    Voice chat endpoint.
    Expects JSON: { "transcript": "<recognized speech>" }
    Uses 'voice' channel context only.
    """
    user_message = ""
    if request.is_json:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("transcript") or "").strip()

    if not user_message:
        return jsonify({"error": "Missing 'transcript' or unsupported payload"}), 400

    reply = handle_message("voice", user_message)
    return jsonify({"reply": reply})


if __name__ == "__main__":
    # Same port as before so your frontend doesn't change.
    app.run(host="0.0.0.0", port=8089, debug=False)
