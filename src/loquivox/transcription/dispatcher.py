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
from .streaming import PartialCallback, StreamingSession


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

    def streaming_backend(self) -> Optional[TranscriptionBackend]:
        """The primary backend iff it can stream, else None."""
        p = self.primary
        return p if (p and p.supports_streaming) else None

    # -- live streaming -----------------------------------------------------
    def start_stream(
        self, sample_rate: int, on_partial: PartialCallback
    ) -> Optional[StreamingSession]:
        """
        Open a live streaming session on the primary backend, or return None if
        the primary doesn't stream / is unavailable / fails to connect. A None
        result means the caller should fall back to the batch path on stop.
        """
        backend = self.streaming_backend()
        if backend is None:
            return None
        if not backend.is_available():
            print(f"⚠️  {backend.name} streaming unavailable — will batch-fallback")
            return None
        try:
            return backend.start_stream(sample_rate, self.language, on_partial)
        except BackendUnavailable as e:
            print(f"⚠️  {backend.name} stream start failed: {e}")
            return None

    def finish_stream(
        self,
        session: StreamingSession,
        audio_fallback: Optional[np.ndarray],
        sample_rate: int,
    ) -> Optional[str]:
        """
        Finalize a streaming session and return the transcript. If streaming
        yields nothing or fails, batch-transcribe the buffered audio via the
        offline fallback (the buffer is kept during recording for exactly this).
        """
        text: Optional[str] = None
        try:
            text = session.finish()
        except BackendUnavailable as e:
            print(f"⚠️  streaming finish failed: {e}")
        if text:
            return text
        if audio_fallback is not None and len(audio_fallback):
            print("🔻 Streaming produced no text — batch fallback on buffered audio")
            return self._batch_fallback(audio_fallback, sample_rate)
        return None

    def _batch_fallback(self, audio: np.ndarray, sample_rate: int) -> Optional[str]:
        """Transcribe via the offline fallback only (primary is the failed stream)."""
        fb = self.fallback
        if fb is None or not fb.is_available():
            print("❌ No offline fallback available for streaming failure.")
            return None
        try:
            return fb.transcribe(audio, sample_rate, self.language)
        except BackendUnavailable as e:
            print(f"⚠️  fallback {fb.name} failed: {e}")
            return None

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
