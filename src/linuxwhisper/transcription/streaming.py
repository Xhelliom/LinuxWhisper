"""
Streaming transcription primitives shared by the live backends.

A ``StreamingSession`` is opened at the start of a recording, fed audio chunks
as they are captured, and finalized at the end to yield the full transcript.
Interim (partial) results are pushed to an ``on_partial`` callback so the
overlay can show text appearing live.

Audio contract: ``feed`` receives float32 mono at the session's sample rate;
each backend converts to the wire format it needs (both current providers use
little-endian 16-bit PCM, hence ``float32_to_pcm16``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

import numpy as np

#: callback signature: receives the best-so-far transcript (finals + interim)
PartialCallback = Callable[[str], None]


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    """Convert a float32 [-1, 1] mono array to little-endian 16-bit PCM bytes."""
    if audio.ndim > 1:
        audio = audio.reshape(audio.shape[0], -1).mean(axis=1)
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


class StreamingSession(ABC):
    """A live transcription session for a single recording."""

    @abstractmethod
    def feed(self, audio: np.ndarray) -> None:
        """Push a captured audio chunk (float32 mono @ session rate). Non-blocking."""

    @abstractmethod
    def finish(self, timeout: float = 8.0) -> Optional[str]:
        """
        Signal end-of-audio, wait (up to ``timeout`` s) for the final transcript,
        and return it (or ``None`` if nothing was transcribed). Raise
        ``BackendUnavailable`` if the session failed operationally.
        """

    def close(self) -> None:
        """Best-effort teardown; safe to call multiple times."""
        return None
