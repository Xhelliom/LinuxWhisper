"""
Mutable application state and settings persistence.
"""
from __future__ import annotations

import json
import queue
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import sounddevice as sd

from loquivox.config import CFG

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

# AyatanaAppIndicator3 is optional — may not be available on all systems.
try:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
    HAS_APP_INDICATOR = True
except (ValueError, ImportError):
    AppIndicator = None
    HAS_APP_INDICATOR = False


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------
class SettingsManager:
    """Handles persistence of user settings."""

    @staticmethod
    def load() -> Dict[str, Any]:
        """Load settings from JSON file."""
        if not CFG.SETTINGS_FILE.exists():
            return {}
        try:
            with open(CFG.SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to load settings: {e}")
            return {}

    @staticmethod
    def save(state: "AppState") -> None:
        """Save current relevant state to JSON file."""
        try:
            CFG.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "color_scheme": state.color_scheme,
                "tts_voice": state.tts_voice,
                "tts_enabled": state.tts_enabled,
                "chat_pinned": state.chat_pinned,
                "chat_enabled": state.chat_enabled,
                "toggle_mode": state.toggle_mode,
            }
            with open(CFG.SETTINGS_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"⚠️ Failed to save settings: {e}")


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
@dataclass
class AppState:
    """
    Mutable application state.

    All runtime state is centralized here for clarity and debugging.
    Reset by creating a new instance: STATE = AppState()
    """
    # --- Recording State ---
    recording: bool = False
    current_mode: Optional[str] = None
    audio_buffer: List[np.ndarray] = field(default_factory=list)
    stream: Optional[sd.InputStream] = None
    viz_queue: queue.Queue = field(default_factory=queue.Queue)
    # Sample rate the current recording is captured at. Normally CFG.SAMPLE_RATE,
    # but a streaming backend captures at its own wire rate (e.g. 16/24 kHz).
    capture_rate: int = CFG.SAMPLE_RATE
    # Active live-transcription session (StreamingSession) while a streaming
    # backend is recording; None for the batch path.
    stream_session: Optional[Any] = None
    # Monotonic counter bumped on each new recording; used to discard a
    # transcription whose recording was superseded before it returned.
    recording_generation: int = 0
    # True while a recording is paused: audio is neither buffered nor streamed,
    # but the input stream stays open so capture can resume instantly.
    paused: bool = False

    # --- UI Windows ---
    overlay_window: Optional[Any] = None   # GtkOverlay instance
    chat_overlay_window: Optional[Any] = None  # ChatOverlay instance

    # --- Chat State ---
    chat_messages: List[Dict[str, str]] = field(default_factory=list)
    chat_pinned: bool = False
    chat_enabled: bool = True
    chat_hide_timer: Optional[int] = None
    # True while the chat overlay's text input has focus — suppresses auto-hide
    # so the overlay can't vanish mid-typing.
    chat_input_focused: bool = False
    # True while a typed-chat request is in flight — serializes submissions so
    # two concurrent workers can't deliver answers out of order.
    chat_busy: bool = False

    # --- History ---
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    answer_history: List[Dict[str, str]] = field(default_factory=list)

    # --- TTS ---
    tts_enabled: bool = False  # Disabled by default
    tts_voice: str = CFG.TTS_DEFAULT_VOICE

    # --- Hotkey Mode ---
    toggle_mode: bool = False  # False = hold-to-record, True = press-to-toggle

    # --- UI Theme ---
    color_scheme: str = CFG.DEFAULT_SCHEME

    # --- System Tray ---
    indicator: Optional[Any] = None  # AppIndicator.Indicator or None
    gtk_menu: Optional[Gtk.Menu] = None

    # --- UI Persistence ---
    last_chat_position: Optional[Tuple[int, int]] = None

    def __post_init__(self):
        """Load persistent settings after initialization."""
        saved = SettingsManager.load()
        if "color_scheme" in saved:
            self.color_scheme = saved["color_scheme"]
        if "tts_voice" in saved:
            self.tts_voice = saved["tts_voice"]
        if "tts_enabled" in saved:
            self.tts_enabled = saved["tts_enabled"]
        if "chat_pinned" in saved:
            self.chat_pinned = saved["chat_pinned"]
        if "chat_enabled" in saved:
            self.chat_enabled = saved["chat_enabled"]
        if "toggle_mode" in saved:
            self.toggle_mode = saved["toggle_mode"]


# Global state instance
STATE = AppState()
