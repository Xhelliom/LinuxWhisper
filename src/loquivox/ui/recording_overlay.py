"""
Floating recording overlay with a smoothed EQ-style waveform.

On Wayland: uses gtk-layer-shell for proper overlay behaviour.
On X11: uses classic GTK window hints (POPUP, keep-above).

Animation notes:
- Fade + slide are done entirely in cairo (every paint is scaled by an opacity
  that eases 0→1 on show and →0 on close, and the content is translated a few
  px), so it looks smooth regardless of whether the compositor honours
  per-window opacity on a layer-shell surface.
- The bars are temporally smoothed: each frame eases toward a target (fast
  attack, slow decay) so the waveform never jumps, and gently "breathes" when
  there's no sound.
"""
from __future__ import annotations

import math
import queue
from typing import List, Tuple

import cairo
import numpy as np

from loquivox.config import CFG
from loquivox.platform import SESSION_TYPE
from loquivox.state import STATE

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

# Optional gtk-layer-shell for Wayland
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False


class GtkOverlay(Gtk.Window):
    """Floating recording overlay with a smoothed EQ-style waveform."""

    NUM_BARS = 28
    MAX_BAR = 10          # px half-height of the tallest bar (kept short so the
                          # text band above can grow to two lines within OVERLAY_HEIGHT)
    FRAME_MS = 16         # ~60 fps
    SLIDE_PX = 10         # how far the content slides up while fading in

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
            GtkLayerShell.set_namespace(self, "loquivox-recording")
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
        """Setup drawing area and animation state."""
        self.transcribing = False
        self.paused = False
        self.live_text = ""
        # Refinement chooser state (grows the overlay while picking a level).
        from loquivox.config import POSTPROCESS_LEVELS
        self.choosing = False
        self.choose_level = 0
        self._base_w, self._base_h = CFG.OVERLAY_WIDTH, CFG.OVERLAY_HEIGHT
        self._choose_w = max(CFG.OVERLAY_WIDTH, 280)
        self._choose_h = 40 + len(POSTPROCESS_LEVELS) * 22 + 26  # title + rows + hint
        self._tick = 0
        self._last_audio_tick = 0
        self._opacity = 0.0           # eases 0→1 on show
        self._closing = False
        self._bars: List[float] = [0.0] * self.NUM_BARS
        self._targets: List[float] = [0.0] * self.NUM_BARS

        # Use the desktop's UI font (portable, no font dependency to install).
        settings = Gtk.Settings.get_default()
        fontname = (settings.get_property("gtk-font-name") if settings else None) or "Sans 10"
        self._font_family = Pango.FontDescription(fontname).get_family() or "Sans"

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_size_request(CFG.OVERLAY_WIDTH, CFG.OVERLAY_HEIGHT)
        self.drawing_area.connect("draw", self._on_draw)
        self.add(self.drawing_area)
        self.timeout_id = GLib.timeout_add(self.FRAME_MS, self._animate)

    def set_transcribing(self) -> None:
        """Switch the overlay to the post-recording 'transcribing' state."""
        if self.choosing:  # leaving the chooser → shrink back to normal size
            self.choosing = False
            self._resize(self._base_w, self._base_h)
        self.transcribing = True
        self.drawing_area.queue_draw()

    def set_choosing(self, level: int) -> None:
        """Enter the refinement chooser: grow taller and show the levels."""
        self.choosing = True
        self.transcribing = False
        self.choose_level = int(level)
        self._resize(self._choose_w, self._choose_h)
        self.drawing_area.queue_draw()

    def _resize(self, w: int, h: int) -> None:
        """Resize the overlay window + drawing area, keeping it bottom-centered."""
        self.drawing_area.set_size_request(w, h)
        self.resize(w, h)
        self.queue_resize()
        if not (HAS_LAYER_SHELL and SESSION_TYPE == "wayland"):
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            g = monitor.get_geometry()
            self.move((g.width - w) // 2, g.height - h - 80)

    def set_live_text(self, text: str) -> None:
        """Update the live partial-transcript text shown while streaming."""
        self.live_text = text or ""
        self.drawing_area.queue_draw()

    def set_paused(self, paused: bool) -> None:
        """Toggle the 'paused' indicator (bars freeze, text shows Paused)."""
        self.paused = paused
        self.drawing_area.queue_draw()

    # ---------------------------------------------------------------- anim
    def _animate(self) -> bool:
        """Per-frame tick (~60 fps): ease opacity + bars, then repaint."""
        self._tick += 1

        # Opacity easing (fade in on show, fade out on close) — gentle.
        target = 0.0 if self._closing else 1.0
        self._opacity += (target - self._opacity) * 0.14
        if self._closing and self._opacity < 0.03:
            self.timeout_id = None
            self.destroy()
            return False  # removes this timeout source

        # Freeze the waveform while transcribing (pulse instead) or paused.
        if not self.transcribing and not self.paused:
            self._update_bars()
        self.drawing_area.queue_draw()
        return True

    def _update_bars(self) -> None:
        """Compute new bar targets from the latest audio, then ease toward them."""
        data = None
        while not STATE.viz_queue.empty():
            try:
                data = STATE.viz_queue.get_nowait()
            except queue.Empty:
                break

        n = self.NUM_BARS
        if data is not None and len(data) > 0:
            self._last_audio_tick = self._tick
            step = max(1, len(data) // n)
            for i in range(n):
                seg = data[i * step:(i + 1) * step]
                amp = float(np.max(np.abs(seg))) if len(seg) else 0.0
                # Perceptual shaping: sqrt-ish so quiet speech is visible and
                # loud peaks saturate gracefully instead of clipping hard.
                self._targets[i] = min(1.0, (amp * 6.0) ** 0.6)
        elif self._tick - self._last_audio_tick > 14:
            # Idle → slow, gentle breathing wave across the bars.
            for i in range(n):
                self._targets[i] = 0.05 + 0.04 * (0.5 + 0.5 * math.sin(self._tick * 0.045 + i * 0.45))
        else:
            # Between audio frames: let targets drift down softly.
            for i in range(n):
                self._targets[i] *= 0.93

        # Ease each bar toward its target. Low coefficients = calm, unhurried
        # motion (gentle rise, slow graceful fall).
        for i in range(n):
            t = self._targets[i]
            coef = 0.28 if t > self._bars[i] else 0.08
            self._bars[i] += (t - self._bars[i]) * coef

    @staticmethod
    def _smoothstep(x: float) -> float:
        x = max(0.0, min(1.0, x))
        return x * x * (3 - 2 * x)

    # ---------------------------------------------------------------- draw
    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> None:
        """Draw overlay content (everything scaled by the fade opacity)."""
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        a = self._smoothstep(self._opacity)

        # Clear to fully transparent first (we own the surface).
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Slide the content up as it fades in.
        cr.translate(0, (1.0 - a) * self.SLIDE_PX)

        scheme = CFG.COLOR_SCHEMES.get(STATE.color_scheme, CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME])

        # Refinement chooser takes over the (enlarged) overlay while picking.
        if self.choosing:
            self._draw_chooser(cr, w, h, scheme, a)
            return

        # Live transcripts grow from the right, so we keep the full text and let
        # the renderer ellipsize the START — the latest words stay visible and
        # never spill onto the mic icon.
        live = False
        if self.transcribing:
            text = "Transcription…"
        elif self.paused:
            text = "Paused ⏸"
        elif self.live_text:
            text = self.live_text
            live = True
        else:
            text = self.config["text"]

        # Shared renderer → the settings preview looks identical to the real bubble.
        self.render_content(
            cr, w, h, scheme=scheme, mode=self.mode, text=text,
            bars=self._bars, tick=self._tick, font_family=self._font_family,
            transcribing=self.transcribing, a=a, ellipsize_start=live,
        )

    @classmethod
    def render_content(cls, cr, w, h, *, scheme, mode, text, bars, tick,
                       font_family, transcribing=False, a=1.0,
                       ellipsize_start=False):
        """
        Paint the overlay bubble at (0, 0, w, h). Pure of widget state so the
        settings dialog can render an identical preview by passing its own
        scheme / looping bars.
        """
        config = CFG.MODES.get(mode, CFG.MODES["dictation"])
        bg_rgb = cls._hex_to_rgb(scheme.get(config["bg"], scheme["bg"]))
        fg_rgb = cls._hex_to_rgb(scheme.get(config["fg"], scheme["accent"]))

        # Background rounded rect + subtle accent border.
        cls._rounded_rect_path(cr, 0, 0, w, h, 16)
        cr.set_source_rgba(*bg_rgb, 0.92 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*fg_rgb, 0.18 * a)
        cr.set_line_width(1)
        cr.stroke()

        # Icon (left), text (centered in the area right of the icon, top),
        # activity (bars / pulse, below). Boxing the text right of the icon
        # keeps long live transcripts from spilling over the glyph.
        if transcribing:
            cls._icon_spinner(cr, 30, h / 2, fg_rgb, a, tick)
        else:
            cls._draw_icon(cr, mode, 30, h / 2, fg_rgb, a)
        text_left, text_right = 46, w - 8
        text_w = max(40, text_right - text_left)
        # cy is the *center* of the text band; a one-line label sits centered,
        # a wrapped two-line live transcript fills the band symmetrically. Text
        # band and waveform are balanced so the content is vertically centered
        # (no top-glued text / empty middle).
        cls._draw_text(cr, font_family, text, text_left + text_w / 2, 18, 8.5,
                       fg_rgb, a, max_width=text_w, ellipsize_start=ellipsize_start)
        if transcribing:
            cls._draw_pulse(cr, 58, w - 8, 42, fg_rgb, a, tick)
        else:
            cls._draw_bars(cr, 58, w - 8, 42, fg_rgb, a, bars)

    # ---------------------------------------------------------------- text
    @staticmethod
    def _draw_text(cr, font_family, text, cx, cy, size, color, a,
                   max_width=None, ellipsize_start=False):
        """
        Center text horizontally at cx, vertically at cy, via Pango.

        When ``max_width`` is given the text is constrained to that width and
        wrapped over up to two lines. For ``ellipsize_start`` (a growing live
        transcript) we keep the most recent two lines — a leading ``…`` marks
        text that scrolled off the top — and left-align them so the words flow
        like a teleprompter. Short labels stay centered on a single line.
        """
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(font_family)
        fd.set_size(int(size * Pango.SCALE))
        fd.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        cr.set_source_rgba(*color, a)
        if max_width is not None:
            layout.set_width(int(max_width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            if ellipsize_start:
                # Live transcript: wrap the full text, then keep only the window
                # starting at the second-to-last line so the latest words stay
                # visible. Done by hand (rather than Pango's multi-line START
                # ellipsize, which drops the marker on an interior line) so the
                # "…" sits at the very start and the flow reads top→bottom.
                n = layout.get_line_count()
                if n > 2:
                    start = layout.get_line(n - 2).start_index
                    tail = text.encode("utf-8")[start:].decode("utf-8", "ignore")
                    layout.set_text("… " + tail.lstrip(), -1)
                layout.set_alignment(Pango.Alignment.LEFT)
                # Safety net if the windowed text still spills over two lines.
                layout.set_height(-2)
                layout.set_ellipsize(Pango.EllipsizeMode.END)
            else:
                layout.set_alignment(Pango.Alignment.CENTER)
                layout.set_height(-2)
                layout.set_ellipsize(Pango.EllipsizeMode.END)
            _, th = layout.get_pixel_size()
            cr.move_to(cx - max_width / 2, cy - th / 2)
        else:
            tw, th = layout.get_pixel_size()
            cr.move_to(cx - tw / 2, cy - th / 2)
        PangoCairo.show_layout(cr, layout)

    def _draw_chooser(self, cr, w, h, scheme, a) -> None:
        """Render the refinement-level chooser on the (enlarged) overlay."""
        from loquivox.config import POSTPROCESS_LEVELS
        bg = self._hex_to_rgb(scheme["bg"])
        fg = self._hex_to_rgb(scheme.get("accent", scheme["text"]))
        txt = self._hex_to_rgb(scheme["text"])

        self._rounded_rect_path(cr, 0, 0, w, h, 16)
        cr.set_source_rgba(*bg, 0.96 * a)
        cr.fill_preserve()
        cr.set_source_rgba(*fg, 0.30 * a)
        cr.set_line_width(1)
        cr.stroke()

        self._draw_text(cr, self._font_family, "Choose refinement", w / 2, 20, 9.5, fg, a)

        top, row_h = 38, 22
        for i, (lvl, label) in enumerate(POSTPROCESS_LEVELS):
            y = top + i * row_h
            selected = (lvl == self.choose_level)
            if selected:
                self._rounded_rect_path(cr, 10, y, w - 20, row_h - 3, 7)
                cr.set_source_rgba(*fg, 0.92 * a)
                cr.fill()
                row_color, weight = bg, Pango.Weight.BOLD
            else:
                row_color, weight = txt, Pango.Weight.NORMAL
            mid = y + (row_h - 3) / 2
            self._draw_text_at(cr, self._font_family, str(lvl), 22, mid, 8.5, row_color, a, weight)
            self._draw_text_at(cr, self._font_family, label, 48, mid, 8.5, row_color, a, weight)

        self._draw_text(cr, self._font_family, "0-5 / ↑↓  ·  Enter ✓  ·  Esc ✗",
                        w / 2, h - 13, 7.5, fg, a)

    @staticmethod
    def _draw_text_at(cr, font_family, text, x, cy, size, color, a,
                      weight=Pango.Weight.NORMAL):
        """Left-aligned text at x, vertically centered at cy."""
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(font_family)
        fd.set_size(int(size * Pango.SCALE))
        fd.set_weight(weight)
        layout.set_font_description(fd)
        layout.set_text(text, -1)
        _, th = layout.get_pixel_size()
        cr.set_source_rgba(*color, a)
        cr.move_to(x, cy - th / 2)
        PangoCairo.show_layout(cr, layout)

    # --------------------------------------------------------------- icons
    @classmethod
    def _draw_icon(cls, cr, mode, cx, cy, color, a):
        """Dispatch to a vector glyph for the recording mode."""
        cr.set_source_rgba(*color, a)
        cr.set_line_width(1.8)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        drawer = {
            "dictation": cls._icon_mic,
            "ai": cls._icon_sparkle,
            "ai_rewrite": cls._icon_pencil,
            "vision": cls._icon_camera,
        }.get(mode, cls._icon_mic)
        drawer(cr, cx, cy)

    @staticmethod
    def _icon_mic(cr, cx, cy):
        r = 3.6
        top, bot = cy - 9, cy - 1
        cr.new_sub_path()
        cr.arc(cx, top + r, r, math.pi, 2 * math.pi)
        cr.arc(cx, bot - r, r, 0, math.pi)
        cr.close_path()
        cr.stroke()
        cr.arc(cx, cy - 3, 6.5, math.radians(20), math.radians(160))
        cr.stroke()
        cr.move_to(cx, cy + 3.5)
        cr.line_to(cx, cy + 7)
        cr.stroke()
        cr.move_to(cx - 4, cy + 7)
        cr.line_to(cx + 4, cy + 7)
        cr.stroke()

    @staticmethod
    def _icon_pencil(cr, cx, cy):
        cr.move_to(cx - 6, cy + 6)
        cr.line_to(cx + 4, cy - 4)
        cr.stroke()
        cr.move_to(cx + 4, cy - 4)
        cr.line_to(cx + 7, cy - 7)
        cr.stroke()
        cr.move_to(cx - 6, cy + 6)
        cr.line_to(cx - 3, cy + 6.5)
        cr.stroke()

    @classmethod
    def _icon_camera(cls, cr, cx, cy):
        cr.move_to(cx - 3, cy - 6)
        cr.line_to(cx + 1, cy - 6)
        cr.stroke()
        cls._rounded_rect_path(cr, cx - 9, cy - 5, 18, 12, 2.5)
        cr.stroke()
        cr.arc(cx, cy + 1, 3.4, 0, 2 * math.pi)
        cr.stroke()

    @staticmethod
    def _icon_sparkle(cr, cx, cy):
        s, ww = 9.0, 2.6
        cr.move_to(cx, cy - s)
        cr.curve_to(cx + ww, cy - ww, cx + ww, cy - ww, cx + s, cy)
        cr.curve_to(cx + ww, cy + ww, cx + ww, cy + ww, cx, cy + s)
        cr.curve_to(cx - ww, cy + ww, cx - ww, cy + ww, cx - s, cy)
        cr.curve_to(cx - ww, cy - ww, cx - ww, cy - ww, cx, cy - s)
        cr.close_path()
        cr.fill()

    @staticmethod
    def _icon_spinner(cr, cx, cy, color, a, tick):
        """Rotating arc spinner for the transcribing state."""
        cr.set_line_width(2.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        start = (tick * 0.12) % (2 * math.pi)
        cr.set_source_rgba(*color, a)
        cr.arc(cx, cy, 7, start, start + math.radians(270))
        cr.stroke()

    @staticmethod
    def _rounded_rect_path(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @classmethod
    def _draw_bars(cls, cr, x1, x2, cy, color, a, bars):
        """Draw the smoothed, mirrored EQ bars from a list of 0..1 levels."""
        n = len(bars)
        slot = (x2 - x1) / n
        cr.set_line_width(max(2.0, slot * 0.5))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for i in range(n):
            level = bars[i]
            hh = max(0.6, level * cls.MAX_BAR)
            x = x1 + slot * (i + 0.5)
            cr.set_source_rgba(*color, (0.4 + 0.6 * level) * a)
            cr.move_to(x, cy - hh)
            cr.line_to(x, cy + hh)
            cr.stroke()

    @staticmethod
    def _draw_pulse(cr, x1, x2, cy, color, a, tick):
        """Three pulsing dots to signal transcription in progress."""
        num_dots = 3
        spacing = (x2 - x1) / (num_dots + 1)
        for i in range(num_dots):
            phase = tick / 14.0 - i * 0.7
            alpha = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(phase))
            cr.set_source_rgba(*color, alpha * a)
            cr.arc(x1 + spacing * (i + 1), cy, 4, 0, 2 * math.pi)
            cr.fill()

    @staticmethod
    def _hex_to_rgb(hex_str: str) -> Tuple[float, float, float]:
        """Convert hex color to RGB tuple (0-1 range)."""
        h = hex_str.lstrip('#')
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def close(self) -> None:
        """Begin the fade-out; the animation tick destroys the window at the end."""
        if self._closing:
            return
        self._closing = True  # _animate fades opacity to 0, then destroys

    def close_immediate(self) -> None:
        """
        Destroy the window at once, skipping the ~370 ms fade-out.

        Used right before a Vision screenshot so the overlay never lingers
        in the captured image. Cancels the animation tick first so it can't
        fire on a destroyed window.
        """
        self._closing = True
        if self.timeout_id is not None:
            try:
                GLib.source_remove(self.timeout_id)
            except Exception:
                pass
            self.timeout_id = None
        try:
            self.destroy()
        except Exception:
            pass
