"""
Deepgram live streaming backend → real-time transcription.

Uses the Deepgram Python SDK's synchronous WebSocket (``client.listen.v1``).
The connection's context manager is owned by a dedicated worker thread that
pulls captured audio from a queue and pushes it with ``send_media``; interim and
final results arrive via the MESSAGE event callback.

Optional dependency (``pip install -e '.[deepgram]'``) and ``DEEPGRAM_API_KEY``
in the environment. When either is missing, ``is_available()`` is False and the
dispatcher transparently uses the offline fallback.

Batch transcription is intentionally unsupported here (``transcribe`` raises),
so if the live stream fails the dispatcher's batch chain skips straight to the
offline fallback.
"""
from __future__ import annotations

import os
import queue
import threading
from typing import Optional

import numpy as np

from .base import BackendUnavailable, TranscriptionBackend
from .streaming import PartialCallback, StreamingSession, float32_to_pcm16

_FINISH = object()  # sentinel queued by finish() to end the send loop


class DeepgramSession(StreamingSession):
    """A single live Deepgram WebSocket transcription session."""

    def __init__(self, client, model: str, language: str, sample_rate: int,
                 on_partial: Optional[PartialCallback]) -> None:
        self._queue: "queue.Queue" = queue.Queue()
        self._on_partial = on_partial
        self._finals: list[str] = []
        self._interim = ""
        self._error: Optional[Exception] = None
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(client, model, language, sample_rate), daemon=True
        )
        self._thread.start()

    def _run(self, client, model: str, language: str, sample_rate: int) -> None:
        try:
            from deepgram.core.events import EventType

            opts: dict = {
                "model": model,
                "encoding": "linear16",
                "sample_rate": sample_rate,
                "interim_results": True,
                "punctuate": True,
                # Without a language, Deepgram defaults to English and will
                # mis-transcribe other languages — use multilingual autodetect
                # (nova-3) so e.g. French stays French. A forced language wins.
                "language": language or "multi",
            }

            with client.listen.v1.connect(**opts) as conn:
                conn.on(EventType.MESSAGE, self._on_message)
                conn.on(EventType.ERROR, self._on_error)
                conn.start_listening()
                while True:
                    item = self._queue.get()
                    if item is _FINISH:
                        try:
                            conn.send_close_stream()
                        except Exception:
                            pass
                        break
                    try:
                        conn.send_media(item)
                    except Exception as e:
                        self._error = e
                        break
        except Exception as e:
            self._error = e
        finally:
            self._done.set()

    def _on_message(self, message) -> None:
        text, is_final = _extract_transcript(message)
        if not text:
            return
        if is_final:
            self._finals.append(text)
            self._interim = ""
        else:
            self._interim = text
        if self._on_partial:
            self._on_partial(self._best_text())

    def _on_error(self, error) -> None:
        self._error = error if isinstance(error, Exception) else RuntimeError(str(error))

    def _best_text(self) -> str:
        return " ".join([*self._finals, self._interim]).strip()

    def feed(self, audio: np.ndarray) -> None:
        self._queue.put(float32_to_pcm16(audio))

    def finish(self, timeout: float = 8.0) -> Optional[str]:
        self._queue.put(_FINISH)
        self._done.wait(timeout)
        if self._error:
            raise BackendUnavailable(f"Deepgram stream error: {self._error}")
        return " ".join(self._finals).strip() or None

    def close(self) -> None:
        if not self._done.is_set():
            self._queue.put(_FINISH)


def _extract_transcript(message) -> tuple[str, bool]:
    """Pull (transcript, is_final) out of a Deepgram results message, defensively."""
    try:
        alt = message.channel.alternatives[0]
        text = getattr(alt, "transcript", "") or ""
    except Exception:
        return "", False
    is_final = bool(getattr(message, "is_final", False))
    return text.strip(), is_final


class DeepgramBackend(TranscriptionBackend):
    """Streaming live transcription via Deepgram."""

    name = "deepgram"
    supports_streaming = True
    stream_sample_rate = 16000

    def __init__(self, model: str) -> None:
        self._model = model

    def is_available(self) -> bool:
        if not os.environ.get("DEEPGRAM_API_KEY"):
            return False
        try:
            import deepgram  # noqa: F401
        except ImportError:
            return False
        return True

    def _client(self):
        try:
            from deepgram import DeepgramClient
        except ImportError as e:
            raise BackendUnavailable(
                "deepgram-sdk not installed — run pip install -e '.[deepgram]'"
            ) from e
        return DeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])

    def start_stream(self, sample_rate: int, language: str,
                     on_partial: PartialCallback) -> StreamingSession:
        return DeepgramSession(self._client(), self._model, language, sample_rate, on_partial)

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> Optional[str]:
        # Streaming-only here: force the dispatcher's offline batch fallback.
        raise BackendUnavailable("deepgram is streaming-only; use start_stream")
