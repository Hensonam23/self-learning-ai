"""Helpers to silence ALSA/JACK errors for cleaner logs."""
from __future__ import annotations

import ctypes
from ctypes import c_char_p, c_int, CFUNCTYPE
import os

# Custom error handler that ignores ALSA messages.
ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)

def _alsa_error_handler(filename, line, function, err, fmt):
    """No-op error handler for ALSA."""
    pass

_c_handler = ERROR_HANDLER_FUNC(_alsa_error_handler)

_defused = False

def silence_alsa() -> None:
    """Install no-op handlers for ALSA and JACK to suppress noisy logs."""
    global _defused
    if _defused:
        return
    _defused = True

    try:
        ctypes.cdll.LoadLibrary("libasound.so").snd_lib_error_set_handler(_c_handler)
    except Exception:
        pass

    # Prevent PortAudio from attempting to start JACK
    os.environ.setdefault("JACK_NO_START_SERVER", "1")
