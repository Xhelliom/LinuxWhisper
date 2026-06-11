"""
OpenAI Realtime streaming backend → real-time transcription.

The OpenAI Realtime SDK is async (``AsyncOpenAI().realtime.connect``), so the
session runs an asyncio event loop in a dedicated thread. Captured audio is
handed across via ``asyncio.run_coroutine_threadsafe``; transcription deltas
arrive as ``conversation.item.input_audio_transcription.delta`` events and the
final text as ``...transcription.completed``.

OpenAI Realtime audio is 24 kHz mono PCM16 → ``stream_sample_rate = 24000`` so
capture matches the wire format with no resampling.

Optional dependency (``pip install -e '.[openai]'``) and ``OPENAI_API_KEY`` in
the environment. Missing either → ``is_available()`` False → offline fallback.
Batch ``transcribe`` raises so a stream failure lands on the offline fallback.
"""
from __future__ import annotations

import asyncio
import base64
import os
import threading
from typing import Optional

import numpy as np

from .base import BackendUnavailable, TranscriptionBackend
from .streaming import PartialCallback, StreamingSession, float32_to_pcm16


class OpenAIRealtimeSession(StreamingSession):
    """A single OpenAI Realtime transcription session (async loop in a thread)."""

    def __init__(self, model: str, language: str, on_partial: Optional[PartialCallback]) -> None:
        self._model = model
        self._language = language
        self._on_partial = on_partial
        self._interim = ""
        self._final = ""
        self._error: Optional[Exception] = None
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._conn = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait until the session is configured (or failed) before feeding audio.
        self._ready.wait(timeout=10.0)
        if self._error:
            raise BackendUnavailable(f"OpenAI Realtime connect failed: {self._error}")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session())
        except Exception as e:
            self._error = e
            self._ready.set()
        finally:
            self._finished.set()
            self._loop.close()

    async def _session(self) -> None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        transcription: dict = {"model": self._model}
        if self._language:
            transcription["language"] = self._language
        try:
            # A transcription-only session connects with intent=transcription
            # (NOT a realtime model — passing gpt-4o-transcribe to connect is
            # rejected as invalid_model). The transcription model goes in the
            # session config below.
            async with client.realtime.connect(
                extra_query={"intent": "transcription"}
            ) as conn:
                self._conn = conn
                # GA Realtime transcription-session shape (openai>=2): audio
                # config is nested under audio.input; format is 24 kHz PCM.
                await conn.session.update(session={
                    "type": "transcription",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "transcription": transcription,
                            "turn_detection": None,  # push-to-talk: commit manually
                        }
                    },
                })
                self._ready.set()
                async for event in conn:
                    if self._handle_event(event):
                        break
        except Exception as e:
            self._error = e
            self._ready.set()

    def _handle_event(self, event) -> bool:
        """Return True to stop the event loop (transcription complete)."""
        etype = getattr(event, "type", "")
        if etype.endswith("input_audio_transcription.delta"):
            self._interim += getattr(event, "delta", "") or ""
            if self._on_partial:
                self._on_partial(self._interim.strip())
        elif etype.endswith("input_audio_transcription.completed"):
            self._final = (getattr(event, "transcript", "") or self._interim).strip()
            return True
        elif etype.endswith("input_audio_transcription.failed") or etype == "error":
            self._error = RuntimeError(getattr(event, "error", etype))
            return True
        return False

    def _submit(self, coro) -> None:
        if self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def feed(self, audio: np.ndarray) -> None:
        if self._conn is None or self._error:
            return
        b64 = base64.b64encode(float32_to_pcm16(audio)).decode("ascii")
        self._submit(self._conn.input_audio_buffer.append(audio=b64))

    async def _commit(self) -> None:
        await self._conn.input_audio_buffer.commit()

    def finish(self, timeout: float = 8.0) -> Optional[str]:
        if self._error:
            raise BackendUnavailable(f"OpenAI Realtime error: {self._error}")
        if self._conn is not None:
            self._submit(self._commit())
        # Wait for the .completed event (which ends the loop) or timeout.
        self._finished.wait(timeout)
        self.close()
        if self._error:
            raise BackendUnavailable(f"OpenAI Realtime error: {self._error}")
        return (self._final or self._interim).strip() or None

    def close(self) -> None:
        self._stop = True
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)


class OpenAIRealtimeBackend(TranscriptionBackend):
    """Streaming live transcription via the OpenAI Realtime API."""

    name = "openai_realtime"
    supports_streaming = True
    stream_sample_rate = 24000  # OpenAI Realtime input is 24 kHz PCM16 mono

    def __init__(self, model: str) -> None:
        self._model = model

    def is_available(self) -> bool:
        if not os.environ.get("OPENAI_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def start_stream(self, sample_rate: int, language: str,
                     on_partial: PartialCallback) -> StreamingSession:
        try:
            import openai  # noqa: F401
        except ImportError as e:
            raise BackendUnavailable(
                "openai not installed — run pip install -e '.[openai]'"
            ) from e
        return OpenAIRealtimeSession(self._model, language, on_partial)

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> Optional[str]:
        # Streaming-only here: force the dispatcher's offline batch fallback.
        raise BackendUnavailable("openai_realtime is streaming-only; use start_stream")
