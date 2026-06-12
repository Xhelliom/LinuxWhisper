"""
Unified handler for all recording modes.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from loquivox.config import CFG
from loquivox.decorators import run_on_main_thread
from loquivox.managers.chat import ChatManager
from loquivox.managers.history import HistoryManager
from loquivox.managers.overlay import OverlayManager
from loquivox.platform import get_clipboard
from loquivox.services.ai import AIService
from loquivox.services.audio import AudioService
from loquivox.services.clipboard import ClipboardService
from loquivox.services.image import ImageService
from loquivox.services.tts import TTSService
from loquivox.state import STATE

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib


class ModeHandler:
    """Unified handler for all recording modes."""

    @staticmethod
    @run_on_main_thread
    def stop_recording_safe() -> None:
        """Safely stop recording and process (callable from any thread)."""
        if not STATE.recording:
            return

        print("🛑 Voice Stop Triggered (Silence)")
        OverlayManager.set_transcribing()
        audio_data = AudioService.stop_recording()

        # Hand off the live session (if any) and clear it so the next recording
        # starts clean.
        session = STATE.stream_session
        STATE.stream_session = None

        if session is not None:
            ModeHandler.process_stream_async(STATE.current_mode, session, audio_data)
        elif audio_data is not None:
            ModeHandler.process_audio_async(STATE.current_mode, audio_data)
        else:
            OverlayManager.hide()

    @staticmethod
    @run_on_main_thread
    def cancel_active() -> None:
        """
        Abort the current recording and/or in-flight transcription without
        inserting any text (bound to the 'cancel' hotkey, default Esc).

        Bumping ``recording_generation`` makes any transcription already running
        in a worker thread fail the stale-guard in ``process()`` — so it is
        dropped before it can paste. The audio stream (if still open) is stopped
        and its buffer discarded. Safe to call from any thread.
        """
        was_active = STATE.recording or STATE.stream_session is not None
        # Supersede any in-flight transcription so its result is discarded.
        STATE.recording_generation += 1
        if STATE.recording:
            try:
                AudioService.stop_recording()  # closes the stream, no processing
            except Exception:
                pass
        STATE.audio_buffer = []
        STATE.stream_session = None
        STATE.current_mode = None
        STATE.paused = False
        OverlayManager.hide()
        if was_active:
            print("✖️  Cancelled — nothing inserted")

    @staticmethod
    def process_audio_async(mode: str, audio_data: np.ndarray,
                            level_override: Optional[int] = None) -> None:
        """
        Transcribe and process audio off the calling thread.

        Safe to call from any thread (e.g. the keyboard listener): the
        blocking Groq call runs in a worker thread and all UI work is
        marshalled back to the GTK main loop via GLib.idle_add.
        ``level_override`` forces a refinement level for this dictation.
        """
        generation = STATE.recording_generation
        threading.Thread(
            target=ModeHandler._process_worker,
            args=(mode, audio_data, generation, level_override),
            daemon=True,
        ).start()

    @staticmethod
    def _process_worker(mode: str, audio_data: np.ndarray, generation: int,
                        level_override: Optional[int] = None) -> None:
        """Worker thread for processing audio."""
        transcribed = None
        try:
            transcribed = AudioService.transcribe(audio_data)
        except Exception:
            pass

        if transcribed:
            transcribed = ModeHandler._maybe_postprocess(mode, transcribed, level_override)
            # Run processing (API calls etc) on the GTK main thread
            GLib.idle_add(lambda: ModeHandler.process(mode, transcribed, generation))
        else:
            # Nothing to insert (too short / failed) — clear the indicator, but
            # only if a newer recording hasn't already taken over the overlay.
            OverlayManager.hide(generation)

    @staticmethod
    def _maybe_postprocess(mode: str, text: str, level_override: Optional[int] = None) -> str:
        """
        Apply optional LLM post-processing to dictation text (refinement level
        or translate). Runs here in the worker thread so the LLM round-trip
        never blocks the GTK loop. No-op unless mode is 'dictation'. A
        ``level_override`` (from the on-the-fly chooser) forces that level.
        """
        if mode != "dictation":
            return text
        from loquivox.services.postprocess import PostProcessor
        return PostProcessor.process(text, level_override)

    @staticmethod
    def process_stream_async(mode: str, session, audio_data: Optional[np.ndarray],
                             level_override: Optional[int] = None) -> None:
        """
        Finalize a live streaming session off-thread, then process the result.

        ``audio_data`` is the buffered recording, kept as the offline fallback
        if the live stream produced nothing (handled inside the dispatcher).
        ``level_override`` forces a refinement level for this dictation.
        """
        generation = STATE.recording_generation
        threading.Thread(
            target=ModeHandler._stream_worker,
            args=(mode, session, audio_data, generation, level_override),
            daemon=True,
        ).start()

    @staticmethod
    def _stream_worker(mode: str, session, audio_data: Optional[np.ndarray],
                       generation: int, level_override: Optional[int] = None) -> None:
        """Worker thread: finalize the stream (with batch fallback) and process."""
        from loquivox.transcription import get_dispatcher

        transcribed = None
        try:
            transcribed = get_dispatcher().finish_stream(
                session, audio_data, STATE.capture_rate
            )
        except Exception:
            pass

        if transcribed:
            transcribed = ModeHandler._maybe_postprocess(mode, transcribed, level_override)
            GLib.idle_add(lambda: ModeHandler.process(mode, transcribed, generation))
        else:
            OverlayManager.hide(generation)

    @staticmethod
    def process(mode: str, transcribed_text: str, generation: Optional[int] = None) -> None:
        """Route to appropriate handler based on mode."""
        # --- Stale Guard ---
        # Drop a result whose recording was superseded by a newer one while the
        # transcription was in flight.
        if generation is not None and generation != STATE.recording_generation:
            print(f"⏭️ Ignored stale transcription (gen {generation}): '{transcribed_text}'")
            # Do NOT hide unconditionally: a newer recording owns the overlay now.
            OverlayManager.hide(generation)
            return

        # --- Hallucination Guard ---
        # Whisper often emits canned phrases ("Thank you", "Merci", "Untertitel")
        # on silence; filter them to prevent weird loops.
        clean = transcribed_text.strip().lower().rstrip(".!?")
        if clean in CFG.HALLUCINATIONS or len(clean) < 2:
            print(f"⚠️ Ignored Hallucination: '{transcribed_text}'")
            OverlayManager.hide(generation)
            return

        handlers = {
            "dictation": ModeHandler._handle_dictation,
            "ai": ModeHandler._handle_ai,
            "ai_rewrite": ModeHandler._handle_ai_rewrite,
            "vision": ModeHandler._handle_vision,
        }
        handler = handlers.get(mode)
        try:
            if handler and transcribed_text:
                handler(transcribed_text)
        finally:
            # Clear the 'transcribing' indicator once insertion is done.
            OverlayManager.hide(generation)

    @staticmethod
    def _handle_dictation(text: str) -> None:
        """Handle dictation mode: transcribe and type."""
        HistoryManager.add_answer(f"[Dictation] {text}")
        ChatManager.add_message("user", f"🎤 {text}")
        ClipboardService.type_text(text)

    @staticmethod
    def _handle_ai(text: str) -> None:
        """Handle AI chat mode: get response and type."""
        response = AIService.chat(text)
        if not response:
            return

        # Update histories
        HistoryManager.add_message("user", text)
        HistoryManager.add_message("assistant", response)
        HistoryManager.add_answer(response)

        # Update chat overlay
        ChatManager.add_message("user", text)
        ChatManager.add_message("assistant", response)

        ClipboardService.type_text(response)
        TTSService.speak(response)

    @staticmethod
    def _handle_ai_rewrite(text: str) -> None:
        """Handle AI rewrite mode: rewrite selected text based on instruction."""
        clipboard = get_clipboard()
        original = clipboard.paste().strip()
        prompt = (
            f"INSTRUCTION:\n{text}\n\n"
            f"ORIGINAL TEXT:\n{original}\n\n"
            "Rewrite the original text based on the instruction. "
            "Output ONLY the finished text, without introduction or formatting."
        )

        response = AIService.chat(prompt)
        if not response:
            return

        # Update histories
        HistoryManager.add_message("user", f"[Rewrite] {text}\nOriginal: {original[:200]}...")
        HistoryManager.add_message("assistant", response)
        HistoryManager.add_answer(response)

        # Update chat overlay
        ChatManager.add_message("user", f"✍️ {text}")
        ChatManager.add_message("assistant", response)

        ClipboardService.paste_text(response)
        TTSService.speak(response)

    @staticmethod
    def _handle_vision(text: str) -> None:
        """Handle vision mode: screenshot + AI analysis."""
        image_b64 = ImageService.take_screenshot()
        if not image_b64:
            return

        response = AIService.vision(text, image_b64)
        if not response:
            return

        # Update histories
        HistoryManager.add_message("user", f"[Screenshot] {text}")
        HistoryManager.add_message("assistant", response)
        HistoryManager.add_answer(response)

        # Update chat overlay
        ChatManager.add_message("user", f"📸 {text}")
        ChatManager.add_message("assistant", response)

        ClipboardService.type_text(response)
        TTSService.speak(response)
