"""
Audio recording and transcription service.
"""
from __future__ import annotations

import queue
from typing import Any, Optional

import numpy as np
import sounddevice as sd

from linuxwhisper.config import CFG
from linuxwhisper.decorators import safe_execute
from linuxwhisper.state import STATE
from linuxwhisper.transcription import get_dispatcher


class AudioService:
    """Audio recording and transcription service."""

    @staticmethod
    def audio_callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Capture audio chunks into buffer while recording."""
        if not STATE.recording:
            return

        data_copy = indata.copy()

        # Always buffer: the batch path needs it, and it's the safety net the
        # streaming path falls back to if the live session fails.
        STATE.audio_buffer.append(data_copy)

        # Feed the live streaming session, if one is active.
        session = STATE.stream_session
        if session is not None:
            try:
                session.feed(data_copy[:, 0])
            except Exception:
                pass

        # Send downsampled data to visualization queue (skip if full)
        try:
            if STATE.viz_queue.qsize() < 5:
                flat_data = data_copy[:, 0][::10]  # Downsample
                STATE.viz_queue.put_nowait(flat_data)
        except Exception:
            pass

    @staticmethod
    def start_recording() -> None:
        """Start audio recording stream (and a live session if the backend streams)."""
        STATE.audio_buffer = []
        STATE.stream_session = None
        AudioService._clear_viz_queue()

        dispatcher = get_dispatcher()
        streaming_backend = dispatcher.streaming_backend()
        # A streaming backend captures at its own wire rate (avoids resampling);
        # everything else stays at the configured capture rate.
        rate = streaming_backend.stream_sample_rate if streaming_backend else CFG.SAMPLE_RATE
        STATE.capture_rate = rate

        STATE.stream = sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype='float32',
            callback=AudioService.audio_callback
        )
        STATE.stream.start()
        STATE.recording = True
        STATE.recording_generation += 1

        # Open the live session AFTER recording is armed so early audio is
        # buffered (and replayable via fallback) even if the session is slow.
        if streaming_backend is not None:
            session = dispatcher.start_stream(rate, AudioService._on_partial)
            STATE.stream_session = session  # None → silently uses batch fallback

    @staticmethod
    def stop_recording() -> Optional[np.ndarray]:
        """Stop recording and return audio data."""
        STATE.recording = False
        if STATE.stream:
            STATE.stream.stop()
            STATE.stream.close()
            STATE.stream = None

        if STATE.audio_buffer:
            return np.concatenate(STATE.audio_buffer, axis=0)
        return None

    @staticmethod
    def _on_partial(text: str) -> None:
        """Live-transcript callback from a streaming session → update overlay."""
        # Late import to avoid a circular import at module load.
        from linuxwhisper.managers.overlay import OverlayManager
        OverlayManager.set_live_text(text)

    @staticmethod
    def _clear_viz_queue() -> None:
        """Clear the visualization queue."""
        while not STATE.viz_queue.empty():
            try:
                STATE.viz_queue.get_nowait()
            except queue.Empty:
                break

    @staticmethod
    @safe_execute("Transcription")
    def transcribe(audio_data: np.ndarray) -> Optional[str]:
        """
        Transcribe a finished recording via the configured backend.

        Backend selection, the short-clip guard and offline fallback all live
        in the dispatcher (see ``linuxwhisper.transcription``); this stays a
        thin entry point so the recording flow is backend-agnostic.
        """
        return get_dispatcher().transcribe(audio_data, STATE.capture_rate)
