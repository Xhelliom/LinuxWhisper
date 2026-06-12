"""
Groq API client — lazily initialized, thread-safe.

The client is created on first use (not at import time) so a missing
GROQ_API_KEY no longer crashes the whole service at startup: the tray stays
up and the error surfaces when transcription/AI is actually invoked.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from groq import Groq

_client: Optional[Groq] = None
_lock = threading.Lock()


class GroqKeyMissing(RuntimeError):
    """Raised when GROQ_API_KEY is unset and the client is needed."""


def has_api_key() -> bool:
    """True if a Groq API key is present in the environment."""
    return bool(os.environ.get("GROQ_API_KEY"))


def get_client() -> Groq:
    """
    Return the shared Groq client, creating it on first use.

    Thread-safe via double-checked locking. Raises ``GroqKeyMissing`` if the
    API key is absent — callers (wrapped in @safe_execute) should surface this
    rather than crash.
    """
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                api_key = os.environ.get("GROQ_API_KEY")
                if not api_key:
                    raise GroqKeyMissing(
                        "GROQ_API_KEY is not set. Add it to your environment "
                        "(e.g. ~/.config/environment.d/loquivox.conf)."
                    )
                _client = Groq(api_key=api_key)
    return _client
