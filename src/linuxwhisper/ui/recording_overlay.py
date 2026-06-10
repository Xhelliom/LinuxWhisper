"""
Floating recording overlay with waveform visualization.

On Wayland: uses gtk-layer-shell for proper overlay behaviour.
On X11: uses classic GTK window hints (POPUP, keep-above).
"""
from __future__ import annotations

import math
import queue
from typing import Tuple

import cairo
import numpy as np

from linuxwhisper.config import CFG
from linuxwhisper.platform import SESSION_TYPE
from linuxwhisper.state import STATE

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk

# Optional gtk-layer-shell for Wayland
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False


class GtkOverlay(Gtk.Window):
    """Floating recording overlay with waveform visualization."""

    def __init__(self, mode: str):
        # Layer-shell requires TOPLEVEL; X11 uses POPUP
        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
        else:
            super().__init__(type=Gtk.WindowType.POPUP)

        self.mode = mode
        self.config = CFG.MODES.get(mode, CFG.MODES["dictation"])
        self._setup_window()
        self._setup_ui()
        self.show_all()

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.set_app_paintable(True)
        self.set_decorated(False)

        # Enable transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        w, h = CFG.OVERLAY_WIDTH, CFG.OVERLAY_HEIGHT

        if HAS_LAYER_SHELL and SESSION_TYPE == "wayland":
            # --- Wayland: gtk-layer-shell ---
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_namespace(self, "linuxwhisper-recording")
            GtkLayerShell.set_exclusive_zone(self, -1)

            # Anchor to bottom center
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 80)

            # No keyboard interaction needed
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.NONE
            )
        else:
            # --- X11: classic approach ---
            self.set_keep_above(True)

            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geometry = monitor.get_geometry()
            x = (geometry.width - w) // 2
            y = geometry.height - h - 80
            self.move(x, y)

        self.set_default_size(w, h)

    def _setup_ui(self) -> None:
        """Setup drawing area and animation."""
        self.transcribing = False
        self.live_text = ""
        self._tick = 0
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_size_request(CFG.OVERLAY_WIDTH, CFG.OVERLAY_HEIGHT)
        self.drawing_area.connect("draw", self._on_draw)
        self.add(self.drawing_area)
        self.timeout_id = GLib.timeout_add(40, self._animate)

    def set_transcribing(self) -> None:
        """Switch the overlay to the post-recording 'transcribing' state."""
        self.transcribing = True
        self.drawing_area.queue_draw()

    def set_live_text(self, text: str) -> None:
        """Update the live partial-transcript text shown while streaming."""
        self.live_text = text or ""
        self.drawing_area.queue_draw()

    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> None:
        """Draw overlay content."""
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        scheme = CFG.COLOR_SCHEMES.get(STATE.color_scheme, CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME])
        bg_rgb = self._hex_to_rgb(scheme.get(self.config["bg"], scheme["bg"]))
        fg_rgb = self._hex_to_rgb(scheme.get(self.config["fg"], scheme["accent"]))

        # Background rounded rect
        self._draw_rounded_rect(cr, w, h, 15)
        cr.set_source_rgba(*bg_rgb, 0.92)
        cr.fill()

        icon = "📝" if self.transcribing else self.config["icon"]
        if self.transcribing:
            text = "Transcription…"
        elif self.live_text:
            # Live partials grow left-to-right; show the trailing window so the
            # most recent words stay visible in the narrow overlay.
            text = self.live_text[-32:]
            if len(self.live_text) > 32:
                text = "…" + text
        else:
            text = self.config["text"]

        # Icon
        cr.set_source_rgb(*fg_rgb)
        cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(20)
        ext = cr.text_extents(icon)
        cr.move_to(30 - ext.width / 2, h / 2 + ext.height / 2)
        cr.show_text(icon)

        # Text
        cr.set_font_size(10)
        cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ext = cr.text_extents(text)
        cr.move_to(110 - ext.width / 2, 20)
        cr.show_text(text)

        # Activity area: pulsing dots while transcribing, waveform while recording
        if self.transcribing:
            self._draw_pulse(cr, 60, 210, 45, fg_rgb)
        else:
            self._draw_waveform(cr, 60, 210, 45, fg_rgb)

    def _draw_rounded_rect(self, cr: cairo.Context, w: int, h: int, r: int) -> None:
        """Draw rounded rectangle path."""
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, 0)
        cr.arc(w - r, h - r, r, 0, math.pi / 2)
        cr.arc(r, h - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw_waveform(self, cr: cairo.Context, x1: int, x2: int, cy: int, color: Tuple[float, ...]) -> None:
        """Draw audio waveform bars."""
        # Get latest audio data
        data = None
        while not STATE.viz_queue.empty():
            try:
                data = STATE.viz_queue.get_nowait()
            except queue.Empty:
                break

        cr.set_source_rgb(*color)
        cr.set_line_width(3)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        if data is not None and len(data) > 0:
            width = x2 - x1
            num_bars = 30
            step = max(1, len(data) // num_bars)
            bar_width = width / num_bars
            max_height = 15

            for i in range(num_bars):
                idx = i * step
                if idx >= len(data):
                    break
                chunk = data[idx:idx + step]
                amp = np.max(np.abs(chunk)) if len(chunk) > 0 else 0
                bar_h = max(1, min(max_height, amp * 40 * max_height))

                x = x1 + i * bar_width
                cr.move_to(x, cy - bar_h)
                cr.line_to(x, cy + bar_h)
                cr.stroke()
        else:
            # Idle line
            cr.set_line_width(2)
            scheme = CFG.COLOR_SCHEMES.get(STATE.color_scheme, CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME])
            idle_rgb = self._hex_to_rgb(scheme["surface"])
            cr.set_source_rgb(*idle_rgb)
            cr.move_to(x1, cy)
            cr.line_to(x2, cy)
            cr.stroke()

    def _draw_pulse(self, cr: cairo.Context, x1: int, x2: int, cy: int, color: Tuple[float, ...]) -> None:
        """Draw three pulsing dots to signal transcription in progress."""
        num_dots = 3
        spacing = (x2 - x1) / (num_dots + 1)
        for i in range(num_dots):
            # Phase-shifted sine per dot for a left-to-right pulse.
            phase = self._tick / 6.0 - i * 0.7
            alpha = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(phase))
            cr.set_source_rgba(*color, alpha)
            cr.arc(x1 + spacing * (i + 1), cy, 4, 0, 2 * math.pi)
            cr.fill()

    def _animate(self) -> bool:
        """Animation tick."""
        self._tick += 1
        self.drawing_area.queue_draw()
        return True

    @staticmethod
    def _hex_to_rgb(hex_str: str) -> Tuple[float, float, float]:
        """Convert hex color to RGB tuple (0-1 range)."""
        h = hex_str.lstrip('#')
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def close(self) -> None:
        """Clean up and destroy."""
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
            self.timeout_id = None
        self.destroy()
