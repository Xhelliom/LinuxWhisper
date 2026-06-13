"""
Recording overlay visibility management.
"""
from __future__ import annotations

from typing import Optional

from loquivox.decorators import run_on_main_thread
from loquivox.state import STATE

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
        from loquivox.ui.recording_overlay import GtkOverlay
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
    def set_choosing(level: int) -> None:
        """Show the refinement-level chooser (enlarged) on the overlay (if shown)."""
        if STATE.overlay_window:
            try:
                STATE.overlay_window.set_choosing(level)
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

    @staticmethod
    def hide_immediate() -> None:
        """
        Destroy the overlay synchronously, skipping the fade-out.

        Unlike ``hide()`` (which marshals onto the GTK loop and fades out over
        ~370 ms), this tears the window down right away. MUST be called from the
        GTK main thread — used before a Vision screenshot so the overlay is gone
        from the captured image.
        """
        win = STATE.overlay_window
        STATE.overlay_window = None
        if win is not None:
            try:
                win.close_immediate()
            except Exception:
                pass
