# learning_shim.py
# Small adapter that routes user text to your answer engine and (optionally)
# streams captions to the display/web via push_ai_caption.

from typing import Optional, Callable

try:
    # use your existing engine; this file only adapts signatures
    from answer_engine import respond  # type: ignore
except Exception:
    respond = None  # we’ll guard below

def handle_intent_or_ack(
    text: str,
    push_ai_caption: Optional[Callable[[str], None]] = None
) -> str:
    """Entry point used by both voice and web. push_ai_caption is optional."""
    text = (text or "").strip()
    if not text:
        return "Say something I can help with."

    # echo the user line to the screen if a pusher is present
    if push_ai_caption:
        push_ai_caption(f"> {text}")

    # Call your answer engine; tolerate old signatures
    out = ""
    try:
        if respond is None:
            out = "I’m online, but my answer engine isn’t loaded."
        else:
            try:
                out = respond(text, push_ai_caption=push_ai_caption)  # new-style engines
            except TypeError:
                out = respond(text)  # old-style engines
    except Exception as e:
        out = f"error: exception — {e}"

    out = (out or "").rstrip()
    if push_ai_caption and out:
        push_ai_caption(out)
    return out
