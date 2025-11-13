import json
import os
import time
from typing import Dict, Optional


class TeachabilityManager:
    """
    Simple teachability layer that works with data/local_knowledge.json
    as a dict of:

        {
          "normalized question text": "canonical answer",
          ...
        }

    Key behavior:
    - Normalize all questions (lowercase, trimmed, collapse spaces).
    - Strip any leading '>' characters so copy-pasted prompts like
      '> what is my pc good for?' map to 'what is my pc good for?'.
    - When you correct an answer, overwrite the entry for the previous question.
    - When you ask again, look up the normalized question and return it.
    """

    def __init__(self, path: str = "data/local_knowledge.json"):
        self.path = path
        self._data: Dict[str, str] = {}
        self._loaded = False

    # ---------- helpers ----------

    def _normalize(self, text: str) -> str:
        # Strip whitespace
        t = text.strip().lower()
        # Strip any number of leading '>' characters and following spaces
        while t.startswith(">"):
            t = t[1:].lstrip()
        # Collapse internal whitespace to single spaces
        return " ".join(t.split())

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    # normalize all existing keys once
                    normalized: Dict[str, str] = {}
                    for k, v in obj.items():
                        nk = self._normalize(str(k))
                        normalized[nk] = str(v)
                    self._data = normalized
                else:
                    # if it's not a dict, back it up and start empty
                    backup = f"{self.path}.corrupt_{int(time.time())}"
                    try:
                        os.replace(self.path, backup)
                    except Exception:
                        pass
                    self._data = {}
            except Exception:
                backup = f"{self.path}.corrupt_{int(time.time())}"
                try:
                    os.replace(self.path, backup)
                except Exception:
                    pass
                self._data = {}
        else:
            self._data = {}

        # ensure parent dir exists
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

        self._loaded = True

    def _save(self) -> None:
        self._ensure_loaded()
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.path)

    # ---------- public API ----------

    def lookup(self, question: str) -> Optional[dict]:
        """
        Return:

            {"question": <normalized_key>, "canonical_explanation": <answer>}

        or None if we don't know this question yet.
        """
        self._ensure_loaded()
        key = self._normalize(question)
        if key not in self._data:
            return None

        return {
            "question": key,
            "canonical_explanation": self._data[key],
        }

    def record_correction(
        self,
        previous_question: Optional[str],
        previous_answer: Optional[str],
        user_message: str,
    ) -> Optional[dict]:
        """
        If user_message looks like a correction to previous_answer,
        store the explanation as the canonical answer for previous_question.
        """
        if not previous_question or not previous_answer:
            return None

        if not self._looks_like_correction(user_message):
            return None

        explanation = self._extract_explanation(user_message)
        if not explanation:
            return None

        self._ensure_loaded()
        key = self._normalize(previous_question)
        self._data[key] = explanation
        self._save()

        return {
            "question": key,
            "canonical_explanation": explanation,
        }

    # ---------- correction heuristics ----------

    def _strip_leading_markers(self, text: str) -> str:
        # Remove leading '>' and spaces
        t = text.strip()
        while t.startswith(">"):
            t = t[1:].lstrip()
        return t

    def _looks_like_correction(self, user_message: str) -> bool:
        t = self._strip_leading_markers(user_message)
        text = t.lower()

        negative_starts = (
            "no,", "no ", "nope", "nah",
            "that's wrong", "thats wrong", "that is wrong",
            "not quite", "actually,", "actually ",
            "correction:",
            "you are wrong", "you're wrong", "youre wrong",
            "this is wrong",
            "that's not right", "thats not right",
        )
        if text.startswith(negative_starts):
            return True

        contains_markers = (
            "that's not correct",
            "thats not correct",
            "isn't correct",
            "is not correct",
            "you missed",
            "you forgot",
            "the correct answer is",
            "the right answer is",
            "it should be",
            "it's actually",
            "its actually",
        )
        return any(m in text for m in contains_markers)

    def _extract_explanation(self, user_message: str) -> str:
        """
        Strip obvious "you're wrong" preamble and return just the explanation.

        Example:
        "No, that's wrong. My PC is a Ryzen 7..." -> "My PC is a Ryzen 7..."
        """
        text = self._strip_leading_markers(user_message)

        # First remove prefixes like "No,", "Nope", "Correction:", etc.
        prefixes = [
            "No,", "no,", "No ", "no ",
            "Nope,", "nope,", "Nope ", "nope ",
            "Actually,", "actually,", "Actually ", "actually ",
            "Correction:", "correction:",
            "You're wrong because", "you're wrong because",
            "You are wrong because", "you are wrong because",
        ]
        for p in prefixes:
            if text.startswith(p):
                text = text[len(p):].lstrip()
                break

        # Then handle "that's wrong..." as a whole sentence
        lower = text.lower()
        if lower.startswith("that's wrong") or lower.startswith("thats wrong"):
            dot_index = text.find(".")
            if dot_index != -1:
                text = text[dot_index + 1 :].lstrip()

        return text.strip()
