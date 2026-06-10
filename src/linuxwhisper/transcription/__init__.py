"""
Pluggable transcription backends.

Public surface:
  - ``get_dispatcher()`` — the process-wide dispatcher singleton. Use this
    instead of importing a backend directly, so backend choice and offline
    fallback stay centralized.
  - ``reconfigure_dispatcher(cfg)`` — rebuild backends from a fresh config
    (settings UI live-apply).
  - ``TranscriptionBackend`` / ``BackendUnavailable`` for implementing or
    handling backends.
"""
from __future__ import annotations

import threading
from typing import Optional

from .base import BackendUnavailable, TranscriptionBackend
from .dispatcher import TranscriptionDispatcher

_dispatcher: Optional[TranscriptionDispatcher] = None
_lock = threading.Lock()


def get_dispatcher() -> TranscriptionDispatcher:
    """Return the shared dispatcher, building it from CFG on first use."""
    global _dispatcher
    if _dispatcher is None:
        with _lock:
            if _dispatcher is None:
                from linuxwhisper.config import CFG
                _dispatcher = TranscriptionDispatcher(CFG)
    return _dispatcher


def reconfigure_dispatcher(cfg) -> None:
    """Apply a new config to the live dispatcher (rebuilds backends)."""
    get_dispatcher().reconfigure(cfg)


__all__ = [
    "BackendUnavailable",
    "TranscriptionBackend",
    "TranscriptionDispatcher",
    "get_dispatcher",
    "reconfigure_dispatcher",
]
