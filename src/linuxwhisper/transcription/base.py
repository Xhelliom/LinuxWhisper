"""
The pluggable transcription backend interface.

A backend turns recorded audio into text. Backends differ in their *capability*
(batch vs streaming) and their *availability* (a cloud backend needs a key and
network; a local backend needs the model on disk). The dispatcher
(see ``dispatcher.py``) uses ``is_available()`` plus the ``BackendUnavailable``
exception to fall back from a primary (e.g. Groq) to an offline backend
(whisper.cpp) transparently.

Contract for ``transcribe``:
  - return the transcript string, or ``None`` if the audio held no speech.
    ``None`` is a *valid* result — the dispatcher does NOT fall back on it.
  - raise ``BackendUnavailable`` for any operational failure (missing key, no
    network, API/HTTP error, model load failure). That — and only that — is
    what triggers the dispatcher's fallback.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class BackendUnavailable(RuntimeError):
    """
    A backend could not serve a request for operational reasons (missing API
    key, no network, API error, model not loadable). Caught by the dispatcher,
    which then tries the configured offline fallback.
    """


class TranscriptionBackend(ABC):
    """Base class for all transcription engines."""

    #: stable identifier used in config.toml and logs
    name: str = "base"
    #: True if the backend can emit partial results live during recording.
    #: Drives the overlay UX (live text vs a "transcribing…" indicator).
    supports_streaming: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        """
        Cheap, non-throwing readiness probe: key present, package importable,
        model reachable. Returning False makes the dispatcher skip straight to
        the fallback without attempting a (slow, failing) request.
        """

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> Optional[str]:
        """
        Transcribe a finished recording (batch path; every backend implements
        this even if it also streams).

        ``audio`` is float32 mono at ``sample_rate``. ``language`` is an
        ISO-639-1 code or "" for autodetect. Return the transcript, ``None`` for
        no speech, or raise ``BackendUnavailable`` on operational failure.
        """

    def prewarm(self) -> None:
        """
        Optional: do any one-time expensive setup ahead of first use (e.g. load
        / download a local model so an offline fallback is actually ready).
        Default is a no-op; safe to call from a background thread.
        """
        return None
