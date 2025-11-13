#!/usr/bin/env python3

"""
Local answer engine for the Machine Spirit.

Very simple on purpose:

- If the prompt contains a "source of truth" section (from teachability),
  we extract your canonical explanation and answer based on that.

- If not, we give a basic fallback answer that you can improve later.
"""


def respond(prompt: str) -> str:
    text = prompt.strip()
    lower = text.lower()

    # Detect teachability prompts coming from brain.py
    if "source of truth:" in lower:
        return _respond_with_teaching(text)

    # Otherwise, fallback
    return _basic_response(text)


def _respond_with_teaching(prompt: str) -> str:
    """
    Prompt looks roughly like:

        You were corrected by the user previously on this topic.
        They gave you this explanation, which is the source of truth:

        <canonical explanation>

        Now answer the user's new question in your own words:

        User: <question>

    We want to grab <canonical explanation> and build a clear answer from it.
    """
    lower = prompt.lower()
    marker = "source of truth:"
    idx = lower.find(marker)
    if idx == -1:
        # Should not happen, but just in case
        return _basic_response(prompt)

    after = prompt[idx + len(marker):].strip()

    # Split off at the "Now answer..." line if present
    split_token = "now answer the user's new question"
    split_idx = after.lower().find(split_token)
    if split_idx != -1:
        canonical = after[:split_idx].strip()
    else:
        canonical = after

    if not canonical:
        return _basic_response(prompt)

    # Clean up leading quotes or artifacts
    canonical = canonical.strip("\n\r\"' ")

    return (
        "Here is what your PC is good for, based on what you taught me:\n\n"
        f"{canonical}\n\n"
        "I am using your earlier explanation as the source of truth so this stays accurate."
    )


def _basic_response(text: str) -> str:
    """
    Generic fallback for when there is no teaching yet.
    You can expand this later into something smarter.
    """
    lowered = text.lower()

    if "pc" in lowered or "computer" in lowered:
        return (
            "Your PC is capable of strong gaming and everyday use. "
            "Once you correct me about its exact specs or role, I will remember that "
            "and use your version as the truth next time."
        )

    if "hello" in lowered or "hi" in lowered:
        return "Greetings. Core systems are online and listening."

    return (
        "I do not have a taught answer for that yet. "
        "If my reply is wrong or weak, correct me in your own words and I will remember it."
    )
