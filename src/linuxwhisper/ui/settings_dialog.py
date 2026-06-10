"""
GTK Settings dialog for voice and hotkey configuration.
"""
from __future__ import annotations

import math
from typing import Optional

import cairo

from linuxwhisper.config import CFG
from linuxwhisper.state import STATE, SettingsManager

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


class SettingsDialog:
    """GTK Settings dialog for voice and hotkey configuration."""

    _instance: Optional[Gtk.Window] = None
    _listbox: Optional[Gtk.ListBox] = None

    # Transcription backends offered in the UI:
    #   (id, label, is_streaming, model config-key, CFG attr holding the model)
    # "auto" / local / cloud-batch show a "transcribing…" indicator; streaming
    # backends show live text.
    _BACKENDS = [
        ("groq",            "Groq — cloud, batch",          False, "model",            "MODEL_WHISPER"),
        ("whispercpp",      "whisper.cpp — local, offline", False, "whispercpp_model", "WHISPERCPP_MODEL"),
        ("deepgram",        "Deepgram — cloud, live",       True,  "deepgram_model",   "DEEPGRAM_MODEL"),
        ("openai_realtime", "OpenAI Realtime — cloud, live", True, "openai_model",     "OPENAI_MODEL"),
        ("auto",            "Auto — Groq if key, else local", False, None,            None),
    ]

    # Live widget handles (set while the dialog is open).
    _backend_combo: Optional[Gtk.ComboBoxText] = None
    _model_entry: Optional[Gtk.Entry] = None
    _lang_entry: Optional[Gtk.Entry] = None
    _fallback_check: Optional[Gtk.CheckButton] = None
    _trans_status: Optional[Gtk.Label] = None

    @classmethod
    def show(cls) -> None:
        """Show settings dialog (singleton)."""
        if cls._instance and cls._instance.get_visible():
            cls._instance.present()
            return

        cls._instance = cls._create_dialog()
        cls._instance.show_all()

    @classmethod
    def _create_dialog(cls) -> Gtk.Window:
        """Create the settings dialog window."""
        dialog = Gtk.Window(title="LinuxWhisper Settings")
        dialog.set_default_size(420, 760)
        dialog.set_resizable(False)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)

        # Main container
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)

        # --- Voice Section ---
        voice_label = Gtk.Label(label="TTS Voice")
        voice_label.set_halign(Gtk.Align.START)
        voice_label.set_markup("<b>TTS Voice</b>")
        vbox.pack_start(voice_label, False, False, 0)

        voice_combo = Gtk.ComboBoxText()
        for voice in CFG.TTS_VOICES:
            voice_combo.append_text(voice.title())
        voice_combo.set_active(CFG.TTS_VOICES.index(STATE.tts_voice) if STATE.tts_voice in CFG.TTS_VOICES else 0)
        voice_combo.connect("changed", cls._on_voice_changed)
        vbox.pack_start(voice_combo, False, False, 0)

        # --- Transcription Section ---
        cls._build_transcription_section(vbox)

        # --- Color Scheme Gallery ---
        scheme_label = Gtk.Label()
        scheme_label.set_halign(Gtk.Align.START)
        scheme_label.set_markup("<b>Color Scheme Gallery</b>")
        vbox.pack_start(scheme_label, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(-1, 280)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)

        cls._listbox = Gtk.ListBox()
        cls._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        cls._listbox.connect("row-activated", cls._on_scheme_selected)

        schemes = list(CFG.COLOR_SCHEMES.keys())
        for name in schemes:
            row = cls._create_theme_row(name)
            cls._listbox.add(row)
            if name == STATE.color_scheme:
                cls._listbox.select_row(row)

        scrolled.add(cls._listbox)
        vbox.pack_start(scrolled, True, True, 0)

        # --- Hotkeys Section ---
        hotkey_label = Gtk.Label()
        hotkey_label.set_halign(Gtk.Align.START)
        hotkey_label.set_markup("<b>Hotkeys</b>")
        vbox.pack_start(hotkey_label, False, False, 10)

        hotkey_grid = Gtk.Grid()
        hotkey_grid.set_column_spacing(15)
        hotkey_grid.set_row_spacing(8)

        hotkeys = []
        display_names = {
            "dictation": "Dictation:",
            "ai": "AI Chat:",
            "ai_rewrite": "Rewrite:",
            "vision": "Vision:",
            "pin": "Pin Chat:",
            "tts": "TTS Toggle:",
        }

        for mode_id, (label, _, _) in CFG.HOTKEY_DEFS.items():
            name = display_names.get(mode_id, mode_id.replace("_", " ").title() + ":")
            hotkeys.append((name, label))

        for i, (name, key) in enumerate(hotkeys):
            name_label = Gtk.Label(label=name)
            name_label.set_halign(Gtk.Align.START)
            key_label = Gtk.Label(label=key)
            key_label.set_halign(Gtk.Align.START)
            key_label.get_style_context().add_class("dim-label")
            hotkey_grid.attach(name_label, 0, i, 1, 1)
            hotkey_grid.attach(key_label, 1, i, 1, 1)

        vbox.pack_start(hotkey_grid, False, False, 0)

        # Info label
        info_label = Gtk.Label()
        info_label.set_markup("<small><i>(Hotkeys are defined in config.py.)</i></small>")
        info_label.set_halign(Gtk.Align.START)
        vbox.pack_start(info_label, False, False, 10)

        # --- Close Button ---
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda w: dialog.destroy())
        vbox.pack_end(close_btn, False, False, 0)

        dialog.add(vbox)
        dialog.connect("destroy", lambda w: setattr(cls, '_instance', None))

        return dialog

    # -----------------------------------------------------------------
    # Transcription section (#15)
    # -----------------------------------------------------------------
    @classmethod
    def _build_transcription_section(cls, vbox: Gtk.Box) -> None:
        """Backend / model / language / fallback controls, applied live."""
        header = Gtk.Label()
        header.set_halign(Gtk.Align.START)
        header.set_markup("<b>Transcription</b>")
        vbox.pack_start(header, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)

        # Backend selector
        grid.attach(cls._row_label("Backend:"), 0, 0, 1, 1)
        cls._backend_combo = Gtk.ComboBoxText()
        current = CFG.BACKEND.strip().lower()
        active_idx = 0
        for i, (bid, label, _stream, _key, _attr) in enumerate(cls._BACKENDS):
            cls._backend_combo.append_text(label)
            if bid == current:
                active_idx = i
        cls._backend_combo.set_active(active_idx)
        cls._backend_combo.connect("changed", cls._on_backend_changed)
        grid.attach(cls._backend_combo, 1, 0, 1, 1)

        # Model entry (meaning depends on the backend)
        grid.attach(cls._row_label("Model:"), 0, 1, 1, 1)
        cls._model_entry = Gtk.Entry()
        cls._model_entry.set_hexpand(True)
        grid.attach(cls._model_entry, 1, 1, 1, 1)

        # Language entry
        grid.attach(cls._row_label("Language:"), 0, 2, 1, 1)
        cls._lang_entry = Gtk.Entry()
        cls._lang_entry.set_placeholder_text("ISO-639-1 (e.g. fr) — empty = autodetect")
        cls._lang_entry.set_text(CFG.WHISPER_LANGUAGE)
        grid.attach(cls._lang_entry, 1, 2, 1, 1)

        vbox.pack_start(grid, False, False, 0)

        # Offline fallback toggle
        cls._fallback_check = Gtk.CheckButton(label="Offline fallback (whisper.cpp) when a backend is unavailable")
        cls._fallback_check.set_active(bool(CFG.FALLBACK_BACKEND))
        vbox.pack_start(cls._fallback_check, False, False, 0)

        # Apply button + status line
        apply_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.connect("clicked", cls._on_apply_transcription)
        apply_row.pack_start(apply_btn, False, False, 0)
        cls._trans_status = Gtk.Label()
        cls._trans_status.set_halign(Gtk.Align.START)
        cls._trans_status.set_line_wrap(True)
        apply_row.pack_start(cls._trans_status, True, True, 0)
        vbox.pack_start(apply_row, False, False, 0)

        cls._sync_model_entry()  # prefill model + capability hint

    @staticmethod
    def _row_label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        return lbl

    @classmethod
    def _selected_backend(cls) -> tuple:
        """Return the _BACKENDS tuple for the currently selected combo row."""
        idx = cls._backend_combo.get_active() if cls._backend_combo else 0
        if idx < 0 or idx >= len(cls._BACKENDS):
            idx = 0
        return cls._BACKENDS[idx]

    @classmethod
    def _sync_model_entry(cls) -> None:
        """Prefill the model field for the selected backend + update the hint."""
        bid, label, is_stream, model_key, cfg_attr = cls._selected_backend()
        if cfg_attr is None:  # auto — model resolved at runtime
            cls._model_entry.set_text("")
            cls._model_entry.set_sensitive(False)
            cls._model_entry.set_placeholder_text("(chosen automatically)")
        else:
            cls._model_entry.set_sensitive(True)
            cls._model_entry.set_text(getattr(CFG, cfg_attr))
        hint = "🔴 live transcription" if is_stream else "📝 batch (transcribing… indicator)"
        cls._set_trans_status(f"{hint}")

    @classmethod
    def _on_backend_changed(cls, _combo: Gtk.ComboBoxText) -> None:
        cls._sync_model_entry()

    @classmethod
    def _set_trans_status(cls, markup: str) -> None:
        if cls._trans_status:
            cls._trans_status.set_markup(f"<small>{markup}</small>")

    @classmethod
    def _on_apply_transcription(cls, _btn: Gtk.Button) -> None:
        """Write [transcription] to config.toml and apply it live."""
        bid, label, is_stream, model_key, cfg_attr = cls._selected_backend()
        language = cls._lang_entry.get_text().strip()
        fallback = "whispercpp" if cls._fallback_check.get_active() else ""

        values = {"backend": bid, "language": language, "fallback": fallback}
        if model_key is not None:
            model = cls._model_entry.get_text().strip()
            if model:
                values[model_key] = model

        # 1) persist (round-trip, comments preserved)
        from linuxwhisper.config_io import ConfigWriteError, update_section
        try:
            update_section("transcription", values)
        except ConfigWriteError as e:
            cls._set_trans_status(f"❌ {e}")
            return

        # 2) apply live: rebuild CFG + reconfigure the dispatcher
        from linuxwhisper import config as config_module
        from linuxwhisper.transcription import get_dispatcher, reconfigure_dispatcher
        fresh = config_module.reload_config()
        reconfigure_dispatcher(fresh)

        # 3) report — warn if the chosen backend can't actually serve right now
        active = get_dispatcher().active
        if active is None:
            cls._set_trans_status("⚠️ Saved. No usable backend — fallback will be used.")
        elif not active.is_available():
            need = "API key / package"
            cls._set_trans_status(
                f"⚠️ Saved & applied, but <b>{active.name}</b> is unavailable "
                f"({need} missing) → offline fallback will be used."
            )
        else:
            cls._set_trans_status(f"✓ Applied live — backend <b>{active.name}</b>.")
        print(f"⚙️ Transcription settings applied: {values}")

    @staticmethod
    def _on_voice_changed(combo: Gtk.ComboBoxText) -> None:
        """Handle voice selection change."""
        voice = combo.get_active_text().lower()
        STATE.tts_voice = voice
        print(f"🎙️ Voice changed to: {voice}")
        SettingsManager.save(STATE)

    @staticmethod
    def _on_scheme_selected(listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Handle theme gallery selection."""
        if not row:
            return
        # Find theme name from child labels
        name = None
        def find_name(child):
            nonlocal name
            if isinstance(child, Gtk.Label) and not child.get_style_context().has_class("dim-label"):
                name = child.get_text()
            elif hasattr(child, "get_children"):
                for c in child.get_children():
                    find_name(c)

        find_name(row.get_child())

        if name in CFG.COLOR_SCHEMES:
            STATE.color_scheme = name
            print(f"🎨 Color scheme changed to: {name}")
            SettingsManager.save(STATE)
            # Late import to avoid circular dependency
            from linuxwhisper.managers.chat import ChatManager
            ChatManager.refresh_overlay()

    @classmethod
    def _create_theme_row(cls, name: str) -> Gtk.ListBoxRow:
        """Create a visual card for a theme in the gallery."""
        scheme = CFG.COLOR_SCHEMES[name]
        row = Gtk.ListBoxRow()
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_margin_top(8)
        hbox.set_margin_bottom(8)

        # --- Preview Swatches ---
        swatch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        colors = [scheme["bg"], scheme["surface"], scheme["accent"], scheme["text"]]
        for hex_color in colors:
            swatch = Gtk.DrawingArea()
            swatch.set_size_request(16, 16)
            swatch.connect("draw", cls._on_draw_gallery_swatch, hex_color)
            swatch_box.pack_start(swatch, False, False, 0)

        hbox.pack_start(swatch_box, False, False, 0)

        # --- Name & Description ---
        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_label = Gtk.Label(label=name)
        name_label.set_halign(Gtk.Align.START)
        name_label.set_markup(f"<b>{name}</b>")

        desc_label = Gtk.Label(label=scheme.get("desc", ""))
        desc_label.set_halign(Gtk.Align.START)
        desc_label.set_line_wrap(True)
        desc_label.set_max_width_chars(30)
        desc_label.get_style_context().add_class("dim-label")
        desc_label.set_markup(f"<small><i>{scheme.get('desc', '')}</i></small>")

        text_vbox.pack_start(name_label, False, False, 0)
        text_vbox.pack_start(desc_label, False, False, 0)
        hbox.pack_start(text_vbox, True, True, 0)

        row.add(hbox)
        return row

    @staticmethod
    def _on_draw_gallery_swatch(widget: Gtk.DrawingArea, cr: cairo.Context, hex_color: str) -> bool:
        """Draw a small color swatch circle in the gallery row."""
        # Convert hex to RGB
        h = hex_color.lstrip('#')
        rgb = tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

        # Draw circle
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.arc(w/2, h/2, min(w, h)/2 - 1, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.15)
        cr.set_line_width(1)
        cr.stroke()
        return True
