"""
Text-to-speech service using Groq Orpheus.
"""
from __future__ import annotations

import threading

import sounddevice as sd
from scipy.io import wavfile

from loquivox.api import get_client
from loquivox.config import CFG
from loquivox.state import STATE


class TTSService:
    """Text-to-speech service using Groq Orpheus."""

    @staticmethod
    def speak(text: str) -> None:
        """Convert text to speech and play (async)."""
        if not STATE.tts_enabled or not text:
            return

        def _speak_thread():
            try:
                response = get_client().audio.speech.create(
                    model=CFG.MODEL_TTS,
                    voice=STATE.tts_voice,
                    input=text[:CFG.TTS_MAX_CHARS],
                    response_format="wav"
                )
                response.write_to_file(CFG.TEMP_TTS_PATH)
                # Play via sounddevice/PortAudio (already a dependency for
                # recording) instead of shelling out to `aplay`, so the app
                # needs no external ALSA binary at runtime.
                samplerate, data = wavfile.read(CFG.TEMP_TTS_PATH)
                sd.play(data, samplerate)
                sd.wait()
            except Exception as e:
                print(f"❌ TTS Error: {e}")

        threading.Thread(target=_speak_thread, daemon=True).start()

    @staticmethod
    def toggle() -> None:
        """Toggle TTS enabled state."""
        STATE.tts_enabled = not STATE.tts_enabled
        # Late import to avoid circular dependency
        from loquivox.managers.chat import ChatManager
        ChatManager.refresh_overlay()
