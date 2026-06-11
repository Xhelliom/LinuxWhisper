"""
Recording overlay visibility management.
"""
from __future__ import annotations

from typing import Optional

from linuxwhisper.decorators import run_on_main_thread
from linuxwhisper.state import STATE

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib


class OverlayManager:
    """Manages recording overlay visibility."""

    @staticmethod
    @run_on_main_thread
    def show(mode: str) -> None:
        """Show overlay for given mode."""
        OverlayManager._show_impl(mode)

    @staticmethod
    def _show_impl(mode: str) -> None:
        # Late import to avoid circular dependency
        from linuxwhisper.ui.recording_overlay import GtkOverlay
        if STATE.overlay_window:
            try:
                STATE.overlay_window.close()
            except Exception:
                pass
        STATE.overlay_window = GtkOverlay(mode)

    @staticmethod
    @run_on_main_thread
    def set_transcribing() -> None:
        """Switch the current overlay to the 'transcribing' state (if shown)."""
        if STATE.overlay_window:
            try:
                STATE.overlay_window.set_transcribing()
            except Exception:
                pass

    @staticmethod
    @run_on_main_thread
    def set_paused(paused: bool) -> None:
        """Reflect the paused/resumed state on the current overlay (if shown)."""
        if STATE.overlay_window:
            try:
                STATE.overlay_window.set_paused(paused)
            except Exception:
                pass

    @staticmethod
    @run_on_main_thread
    def set_live_text(text: str) -> None:
        """Show incremental live-transcription text on the overlay (if shown)."""
        if STATE.overlay_window:
            try:
                STATE.overlay_window.set_live_text(text)
            except Exception:
                pass

    @staticmethod
    @run_on_main_thread
    def hide(generation: Optional[int] = None) -> None:
        """
        Hide the overlay.

        When ``generation`` is given, the overlay is hidden only if it still
        belongs to the current recording (``STATE.recording_generation``). This
        stops a superseded session — whose async transcription / post-process
        finishes *after* a newer recording has already opened its own overlay —
        from tearing down the new session's overlay. Called with no argument
        (e.g. cancel, or the current session finishing) it always hides.
        """
        if generation is not None and generation != STATE.recording_generation:
            return
        OverlayManager._hide_impl()

    @staticmethod
    def _hide_impl() -> None:
        if STATE.overlay_window:
            try:
                STATE.overlay_window.close()
            except Exception:
                pass
            STATE.overlay_window = None
