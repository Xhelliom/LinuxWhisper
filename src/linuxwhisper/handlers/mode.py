"""
Unified handler for all recording modes.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from linuxwhisper.config import CFG
from linuxwhisper.decorators import run_on_main_thread
from linuxwhisper.managers.chat import ChatManager
from linuxwhisper.managers.history import HistoryManager
from linuxwhisper.managers.overlay import OverlayManager
from linuxwhisper.platform import get_clipboard
from linuxwhisper.services.ai import AIService
from linuxwhisper.services.audio import AudioService
from linuxwhisper.services.clipboard import ClipboardService
from linuxwhisper.services.image import ImageService
from linuxwhisper.services.tts import TTSService
from linuxwhisper.state import STATE

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

        if audio_data is not None:
            ModeHandler.process_audio_async(STATE.current_mode, audio_data)
        else:
            OverlayManager.hide()

    @staticmethod
    def process_audio_async(mode: str, audio_data: np.ndarray) -> None:
        """
        Transcribe and process audio off the calling thread.

        Safe to call from any thread (e.g. the keyboard listener): the
        blocking Groq call runs in a worker thread and all UI work is
        marshalled back to the GTK main loop via GLib.idle_add.
        """
        generation = STATE.recording_generation
        threading.Thread(
            target=ModeHandler._process_worker,
            args=(mode, audio_data, generation),
            daemon=True,
        ).start()

    @staticmethod
    def _process_worker(mode: str, audio_data: np.ndarray, generation: int) -> None:
        """Worker thread for processing audio."""
        transcribed = None
        try:
            transcribed = AudioService.transcribe(audio_data)
        except Exception:
            pass

        if transcribed:
            # Run processing (API calls etc) on the GTK main thread
            GLib.idle_add(lambda: ModeHandler.process(mode, transcribed, generation))
        else:
            # Nothing to insert (too short / failed) — clear the indicator.
            OverlayManager.hide()

    @staticmethod
    def process(mode: str, transcribed_text: str, generation: Optional[int] = None) -> None:
        """Route to appropriate handler based on mode."""
        # --- Stale Guard ---
        # Drop a result whose recording was superseded by a newer one while the
        # transcription was in flight.
        if generation is not None and generation != STATE.recording_generation:
            print(f"⏭️ Ignored stale transcription (gen {generation}): '{transcribed_text}'")
            OverlayManager.hide()
            return

        # --- Hallucination Guard ---
        # Whisper often emits canned phrases ("Thank you", "Merci", "Untertitel")
        # on silence; filter them to prevent weird loops.
        clean = transcribed_text.strip().lower().rstrip(".!?")
        if clean in CFG.HALLUCINATIONS or len(clean) < 2:
            print(f"⚠️ Ignored Hallucination: '{transcribed_text}'")
            OverlayManager.hide()
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
            OverlayManager.hide()

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
