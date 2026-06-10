"""
Local whisper.cpp backend (via pywhispercpp).

Runs fully offline, needs no API key, and therefore doubles as the automatic
offline FALLBACK when a cloud backend is unavailable (no network / no key /
API error). The model is loaded lazily, once, and reused (loading is the
expensive part).

pywhispercpp is an OPTIONAL dependency: ``is_available()`` returns False if it
isn't installed, so a misconfigured fallback degrades gracefully instead of
crashing. Install with ``pip install -e '.[local]'``.

⚠️ The model auto-downloads on first load. For the fallback to work when you're
actually offline, the model must already be on disk — see ``prewarm()``, which
the dispatcher calls in the background at startup.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from .base import BackendUnavailable, TranscriptionBackend
from .util import to_mono_16k


class WhisperCppBackend(TranscriptionBackend):
    """Offline transcription via whisper.cpp (pywhispercpp bindings)."""

    name = "whispercpp"
    supports_streaming = False

    def __init__(self, model: str, n_threads: int = 4) -> None:
        self._model_name = model
        self._n_threads = n_threads
        self._model = None  # lazy-loaded Model instance
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        try:
            import pywhispercpp  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_model(self):
        """Load the whisper.cpp model once (thread-safe, double-checked)."""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from pywhispercpp.model import Model
                    except ImportError as e:
                        raise BackendUnavailable(
                            "pywhispercpp not installed — run "
                            "pip install -e '.[local]'"
                        ) from e
                    print(f"🧠 Loading local whisper.cpp model '{self._model_name}'…")
                    self._model = Model(
                        self._model_name,
                        n_threads=self._n_threads,
                        print_progress=False,
                        print_realtime=False,
                    )
        return self._model

    def prewarm(self) -> None:
        """Load (and download on first run) the model so the fallback is ready."""
        try:
            self._get_model()
        except Exception as e:  # never crash startup over a prewarm failure
            print(f"⚠️ whisper.cpp prewarm failed: {e}")

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> Optional[str]:
        try:
            data = to_mono_16k(audio, sample_rate)
            model = self._get_model()
            kwargs: dict = {}
            if language:  # empty string = autodetect
                kwargs["language"] = language
            segments = model.transcribe(data, **kwargs)
        except BackendUnavailable:
            raise
        except Exception as e:
            raise BackendUnavailable(f"whisper.cpp transcription failed: {e}") from e

        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or None
