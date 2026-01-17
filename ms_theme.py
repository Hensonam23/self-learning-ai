#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


CONFIG_DIR = Path.home() / ".config" / "machinespirit"
THEME_PATH = CONFIG_DIR / "theme.json"


@dataclass
class ThemeConfig:
    theme: str = "none"          # "none" disables theming
    intensity: str = "light"     # "light" | "heavy"

    def normalized(self) -> "ThemeConfig":
        t = (self.theme or "none").strip()
        i = (self.intensity or "light").strip().lower()
        if i not in ("light", "heavy"):
            i = "light"
        if t == "":
            t = "none"
        return ThemeConfig(theme=t, intensity=i)


def load_theme() -> ThemeConfig:
    try:
        if not THEME_PATH.exists():
            return ThemeConfig().normalized()
        data = json.loads(THEME_PATH.read_text(encoding="utf-8"))
        cfg = ThemeConfig(
            theme=str(data.get("theme", "none")),
            intensity=str(data.get("intensity", "light")),
        )
        return cfg.normalized()
    except Exception:
        return ThemeConfig().normalized()


def save_theme(theme: str, intensity: str) -> ThemeConfig:
    cfg = ThemeConfig(theme=theme, intensity=intensity).normalized()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    THEME_PATH.write_text(
        json.dumps({"theme": cfg.theme, "intensity": cfg.intensity}, indent=2) + "\n",
        encoding="utf-8",
    )
    return cfg


def theme_is_off(cfg: ThemeConfig) -> bool:
    t = (cfg.theme or "").strip().lower()
    return t in ("", "none", "off", "disabled")


def apply_theme(answer: str, topic: Optional[str] = None, cfg: Optional[ThemeConfig] = None) -> str:
    """
    Keep answers readable. Theme should be a wrapper, not a rewrite.
    """
    if answer is None:
        answer = ""
    answer = str(answer).strip()

    cfg = (cfg or load_theme()).normalized()
    if theme_is_off(cfg) or not answer:
        return answer

    name = cfg.theme.strip()
    mode = cfg.intensity.lower()

    # Special-case: Warhammer 40k (because you explicitly want this)
    lowered = name.lower()
    is_40k = ("warhammer" in lowered) or ("40k" in lowered) or ("warhammer 40,000" in lowered)

    if is_40k:
        if mode == "heavy":
            prefix = f"+++ VOX-CAST // {topic or 'Knowledge'} +++"
            suffix = "+++ END VOX +++"
        else:
            prefix = f"+++ {topic or 'Knowledge'} // By the Omnissiah +++"
            suffix = ""
        out = f"{prefix}\n\n{answer}"
        if suffix:
            out = f"{out}\n\n{suffix}"
        return out.strip()

    # Generic fallback for other themes (still clean)
    if mode == "heavy":
        prefix = f"=== Theme: {name} (heavy) ==="
    else:
        prefix = f"[Theme: {name}]"

    return f"{prefix}\n\n{answer}".strip()


def ui_intensity_choices() -> Dict[str, Dict[str, str]]:
    # short explanations, like you asked
    return {
        "light": {
            "label": "1) Light",
            "desc": "Small flavor, stays very readable (recommended).",
        },
        "heavy": {
            "label": "2) Heavy",
            "desc": "More roleplay voice, still keeps the answer clear.",
        },
    }
