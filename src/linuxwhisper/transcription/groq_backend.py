"""
Groq batch backend — the current/default transcription engine.

Groq's STT API is batch-only (``POST /audio/transcriptions``, no streaming),
so this backend implements only the batch path and reports
``supports_streaming = False`` → the overlay shows a "transcribing…" indicator
rather than live text.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from linuxwhisper.api import GroqKeyMissing, get_client, has_api_key

from .base import BackendUnavailable, TranscriptionBackend
from .util import resample_down, write_wav


class GroqBackend(TranscriptionBackend):
    """Batch transcription via the Groq Cloud Whisper API."""

    name = "groq"
    supports_streaming = False

    def __init__(self, model: str, upload_sample_rate: int) -> None:
        self._model = model
        self._upload_sample_rate = upload_sample_rate

    def is_available(self) -> bool:
        return has_api_key()

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> Optional[str]:
        # Downsample before upload (Whisper runs at 16 kHz → smaller payload,
        # lower latency, no quality loss).
        audio, rate = resample_down(audio, sample_rate, self._upload_sample_rate)
        wav_buffer = write_wav(audio, rate)

        params: dict = {"model": self._model, "file": wav_buffer}
        if language:  # empty string = autodetect
            params["language"] = language

        try:
            transcript = get_client().audio.transcriptions.create(**params)
        except GroqKeyMissing as e:
            raise BackendUnavailable(str(e)) from e
        except Exception as e:  # network / HTTP / API errors → trigger fallback
            raise BackendUnavailable(f"Groq request failed: {e}") from e

        text = (transcript.text or "").strip()
        return text or None
