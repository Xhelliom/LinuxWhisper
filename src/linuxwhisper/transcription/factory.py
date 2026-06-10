"""
Backend factory: maps a config name to a backend instance.

``"auto"`` resolves to the best available batch backend — Groq if a key is
present, otherwise local whisper.cpp — so a fresh install transcribes out of the
box without hand-editing config.toml.

Streaming backends (openai_realtime, deepgram) are introduced in a later phase;
selecting one before then logs a clear warning and returns None (the dispatcher
then relies on the fallback).
"""
from __future__ import annotations

from typing import Optional

from linuxwhisper.api import has_api_key

from .base import TranscriptionBackend
from .groq_backend import GroqBackend
from .whispercpp_backend import WhisperCppBackend


def _resolve_auto() -> str:
    """Pick a concrete backend for ``backend = "auto"``."""
    return "groq" if has_api_key() else "whispercpp"


def make_backend(name: str, cfg) -> Optional[TranscriptionBackend]:
    """Instantiate the backend identified by ``name`` (case-insensitive)."""
    name = (name or "").strip().lower()
    if name == "auto":
        name = _resolve_auto()

    if name == "groq":
        return GroqBackend(model=cfg.MODEL_WHISPER, upload_sample_rate=cfg.UPLOAD_SAMPLE_RATE)
    if name in ("whispercpp", "whisper.cpp", "local"):
        return WhisperCppBackend(model=cfg.WHISPERCPP_MODEL)
    if name in ("openai_realtime", "openai", "deepgram"):
        print(
            f"⚠️ Transcription backend '{name}' (streaming) is not available yet "
            "— relying on the fallback. (Coming in the streaming phase.)"
        )
        return None

    print(f"⚠️ Unknown transcription backend '{name}' — ignored.")
    return None
