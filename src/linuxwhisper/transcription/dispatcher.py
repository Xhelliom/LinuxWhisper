"""
Transcription dispatcher: picks a backend, applies the short-clip guard, and
falls back to an offline backend when the primary is unavailable.

Fallback policy
---------------
For each of [primary, fallback]:
  - if the backend's ``is_available()`` is False → skip it (don't waste a slow,
    doomed request).
  - else call ``transcribe()``:
      * a returned string (or ``None`` for "no speech") is a SUCCESS → return it,
        no fallback. ``None`` does not mean failure.
      * ``BackendUnavailable`` (network/key/API/model error) → try the next one.

The dispatcher is reconfigurable at runtime (``reconfigure``) so the settings UI
can apply model/language changes live without restarting the service.
"""
from __future__ import annotations

import threading
from typing import List, Optional, Tuple

import numpy as np

from .base import BackendUnavailable, TranscriptionBackend
from .factory import make_backend


class TranscriptionDispatcher:
    """Routes audio to the configured backend, with offline fallback."""

    def __init__(self, cfg) -> None:
        self._lock = threading.Lock()
        self._build(cfg)

    # -- configuration ------------------------------------------------------
    def _build(self, cfg) -> None:
        self.language: str = cfg.WHISPER_LANGUAGE
        self.min_audio_sec: float = cfg.MIN_AUDIO_SEC
        self.primary: Optional[TranscriptionBackend] = make_backend(cfg.BACKEND, cfg)
        self.fallback: Optional[TranscriptionBackend] = (
            make_backend(cfg.FALLBACK_BACKEND, cfg) if cfg.FALLBACK_BACKEND else None
        )
        # Don't fall back to the very backend that's already primary.
        if self.fallback and self.primary and self.fallback.name == self.primary.name:
            self.fallback = None

        active = self.primary.name if self.primary else "none"
        fb = self.fallback.name if self.fallback else "none"
        print(f"🎚️  Transcription backend: primary={active}, fallback={fb}")

        # Warm the offline fallback in the background so it's ready when needed.
        self._prewarm_async()

    def reconfigure(self, cfg) -> None:
        """Rebuild backends from a fresh config (settings UI live-apply)."""
        with self._lock:
            self._build(cfg)

    def _prewarm_async(self) -> None:
        target = self.fallback or self.primary
        if target is None:
            return
        threading.Thread(target=target.prewarm, daemon=True).start()

    @property
    def active(self) -> Optional[TranscriptionBackend]:
        """The primary backend — its capability drives the overlay UX."""
        return self.primary

    @property
    def supports_streaming(self) -> bool:
        return bool(self.primary and self.primary.supports_streaming)

    # -- transcription ------------------------------------------------------
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Optional[str]:
        """Transcribe a finished recording, trying primary then fallback."""
        # Short-clip guard (filters key-click noise, saves a round-trip).
        # Backend-agnostic, so it lives here rather than in each backend.
        duration = len(audio) / sample_rate
        if self.min_audio_sec and duration < self.min_audio_sec:
            print(f"⏭️  Ignored {duration:.2f}s clip (< {self.min_audio_sec}s)")
            return None

        chain: List[Tuple[TranscriptionBackend, bool]] = []
        if self.primary is not None:
            chain.append((self.primary, False))
        if self.fallback is not None:
            chain.append((self.fallback, True))

        if not chain:
            print("❌ No transcription backend configured.")
            return None

        for backend, is_fallback in chain:
            if not backend.is_available():
                tag = "fallback" if is_fallback else "primary"
                print(f"⚠️  {backend.name} ({tag}) unavailable — skipping")
                continue
            try:
                if is_fallback:
                    print(f"🔻 Falling back to offline backend: {backend.name}")
                return backend.transcribe(audio, sample_rate, self.language)
            except BackendUnavailable as e:
                print(f"⚠️  {backend.name} failed: {e}")
                continue

        print("❌ All transcription backends failed.")
        return None
