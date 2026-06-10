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
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import GLib, Gtk, Pango, PangoCairo


class SettingsDialog:
    """GTK Settings dialog for voice and hotkey configuration."""

    _instance: Optional[Gtk.Window] = None
    _listbox: Optional[Gtk.ListBox] = None
    _preview_area: Optional[Gtk.DrawingArea] = None
    _preview_tick: int = 0
    _preview_timer = None

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

    # Known model ids per backend (verified against provider docs). The model
    # combo is editable, so a custom/newer id can still be typed.
    _MODELS = {
        "groq":            ["whisper-large-v3-turbo", "whisper-large-v3"],
        "whispercpp":      ["base", "tiny", "small", "medium", "large-v3",
                            "large-v3-turbo", "base.en", "tiny.en", "small.en"],
        "deepgram":        ["nova-3", "nova-2"],
        "openai_realtime": ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"],
    }
    # Provider model-list links shown as a hint per backend.
    _MODEL_DOCS = {
        "groq":            "console.groq.com/docs/speech-to-text",
        "deepgram":        "developers.deepgram.com/docs/models-languages-overview",
        "openai_realtime": "platform.openai.com/docs/guides/realtime-transcription",
        "whispercpp":      "auto-downloads on first use",
    }
    # Common languages offered in the combo (label, ISO-639-1 code). Editable, so
    # any other Whisper-supported code can be typed directly.
    _LANGUAGES = [
        ("Autodetect", ""), ("English", "en"), ("French", "fr"), ("German", "de"),
        ("Spanish", "es"), ("Italian", "it"), ("Portuguese", "pt"), ("Dutch", "nl"),
        ("Russian", "ru"), ("Chinese", "zh"), ("Japanese", "ja"), ("Korean", "ko"),
        ("Arabic", "ar"), ("Hindi", "hi"), ("Polish", "pl"),
    ]

    _whisper_status: Optional[Gtk.Label] = None
    _hotkey_entries: Optional[dict] = None
    _hotkey_status: Optional[Gtk.Label] = None
    _hotkey_capture_btns: Optional[list] = None
    _key_entries: Optional[dict] = None
    _key_status: Optional[Gtk.Label] = None
    _pp_combo: Optional[Gtk.ComboBoxText] = None
    _pp_lang: Optional[Gtk.ComboBoxText] = None
    _pp_lang_label: Optional[Gtk.Label] = None
    _pp_status: Optional[Gtk.Label] = None

    # Post-processing modes offered in the UI/tray: (id, label)
    _POSTPROCESS_MODES = [
        ("none",        "Off"),
        ("correct",     "Correct (spelling/grammar/punctuation)"),
        ("reformulate", "Reformulate (clearer phrasing)"),
        ("translate",   "Translate to…"),
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
        """Create the settings dialog window (tabbed)."""
        dialog = Gtk.Window(title="LinuxWhisper Settings")
        dialog.set_default_size(470, 640)
        dialog.set_resizable(False)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_keep_above(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{m}")(12)

        # Group the (now numerous) settings into tabs instead of one long scroll.
        notebook = Gtk.Notebook()
        notebook.set_scrollable(True)

        trans = cls._page()
        cls._build_transcription_section(trans)
        cls._build_postprocess_section(trans)
        notebook.append_page(cls._scroll(trans), Gtk.Label(label="Transcription"))

        keys = cls._page()
        cls._build_api_keys_section(keys)
        notebook.append_page(cls._scroll(keys), Gtk.Label(label="API Keys"))

        hotkeys = cls._page()
        cls._build_hotkeys_section(hotkeys)
        notebook.append_page(cls._scroll(hotkeys), Gtk.Label(label="Hotkeys"))

        appearance = cls._page()
        cls._build_appearance_page(appearance)
        notebook.append_page(cls._scroll(appearance), Gtk.Label(label="Appearance"))

        root.pack_start(notebook, True, True, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda w: dialog.destroy())
        root.pack_end(close_btn, False, False, 0)

        dialog.add(root)
        dialog.connect("destroy", cls._on_destroy)
        return dialog

    @classmethod
    def _on_destroy(cls, _w: Gtk.Widget) -> None:
        cls._instance = None
        if cls._preview_timer is not None:
            try:
                GLib.source_remove(cls._preview_timer)
            except Exception:
                pass
            cls._preview_timer = None
        cls._preview_area = None

    @classmethod
    def _tick_preview(cls) -> bool:
        """Drive the looping animation of the overlay preview."""
        if cls._instance is None or cls._preview_area is None:
            cls._preview_timer = None
            return False
        cls._preview_tick += 1
        cls._preview_area.queue_draw()
        return True

    @staticmethod
    def _page() -> Gtk.Box:
        """A padded vertical box used as a notebook tab body."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("top", "bottom", "start", "end"):
            getattr(vbox, f"set_margin_{m}")(14)
        return vbox

    @staticmethod
    def _scroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
        """Wrap a tab body so it scrolls if it ever exceeds the window height."""
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(child)
        return sw

    # -----------------------------------------------------------------
    # Appearance tab: TTS voice + colour scheme gallery + overlay preview
    # -----------------------------------------------------------------
    @classmethod
    def _build_appearance_page(cls, vbox: Gtk.Box) -> None:
        # TTS voice
        voice_label = Gtk.Label()
        voice_label.set_halign(Gtk.Align.START)
        voice_label.set_markup("<b>TTS Voice</b>")
        vbox.pack_start(voice_label, False, False, 0)

        voice_combo = Gtk.ComboBoxText()
        for voice in CFG.TTS_VOICES:
            voice_combo.append_text(voice.title())
        voice_combo.set_active(CFG.TTS_VOICES.index(STATE.tts_voice) if STATE.tts_voice in CFG.TTS_VOICES else 0)
        voice_combo.connect("changed", cls._on_voice_changed)
        vbox.pack_start(voice_combo, False, False, 0)

        # Colour scheme gallery
        scheme_label = Gtk.Label()
        scheme_label.set_halign(Gtk.Align.START)
        scheme_label.set_markup("<b>Color Scheme</b>")
        vbox.pack_start(scheme_label, False, False, 0)

        # Live overlay preview so the theme can be judged on the actual bubble.
        cls._preview_area = Gtk.DrawingArea()
        cls._preview_area.set_size_request(-1, 74)
        cls._preview_area.connect("draw", cls._draw_overlay_preview)
        vbox.pack_start(cls._preview_area, False, False, 0)
        # Loop a gentle animation so the preview shows the live effects.
        if cls._preview_timer is None:
            cls._preview_timer = GLib.timeout_add(40, cls._tick_preview)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(-1, 210)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)

        cls._listbox = Gtk.ListBox()
        cls._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        cls._listbox.connect("row-activated", cls._on_scheme_selected)
        for name in CFG.COLOR_SCHEMES.keys():
            row = cls._create_theme_row(name)
            cls._listbox.add(row)
            if name == STATE.color_scheme:
                cls._listbox.select_row(row)
        scrolled.add(cls._listbox)
        vbox.pack_start(scrolled, True, True, 0)

    @classmethod
    def _draw_overlay_preview(cls, widget: Gtk.DrawingArea, cr) -> bool:
        """
        Preview the recording overlay using the SAME renderer as the real bubble
        (GtkOverlay.render_content), so the preview matches exactly.
        """
        from linuxwhisper.ui.recording_overlay import GtkOverlay

        scheme = CFG.COLOR_SCHEMES.get(STATE.color_scheme, CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME])
        bw, bh = CFG.OVERLAY_WIDTH, CFG.OVERLAY_HEIGHT
        aw, ah = widget.get_allocated_width(), widget.get_allocated_height()

        # Looping, calm waveform like the overlay's idle/recording motion.
        t = cls._preview_tick
        bars = [0.12 + 0.62 * (0.5 + 0.5 * math.sin(t * 0.13 + i * 0.5))
                for i in range(GtkOverlay.NUM_BARS)]

        # Desktop UI font (portable), same as the overlay.
        settings = Gtk.Settings.get_default()
        fontname = (settings.get_property("gtk-font-name") if settings else None) or "Sans 10"
        family = Pango.FontDescription(fontname).get_family() or "Sans"

        cr.translate((aw - bw) / 2, max(0, (ah - bh) / 2))  # center the bubble
        GtkOverlay.render_content(
            cr, bw, bh, scheme=scheme, mode="dictation", text="Listening…",
            bars=bars, tick=t, font_family=family, transcribing=False, a=1.0,
        )
        return True

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

        # Model: editable combo — pick a known id or type a custom one.
        grid.attach(cls._row_label("Model:"), 0, 1, 1, 1)
        cls._model_entry = Gtk.ComboBoxText.new_with_entry()
        cls._model_entry.get_child().set_hexpand(True)
        grid.attach(cls._model_entry, 1, 1, 1, 1)

        # Language: editable combo — common languages + "Autodetect".
        grid.attach(cls._row_label("Language:"), 0, 2, 1, 1)
        cls._lang_entry = Gtk.ComboBoxText.new_with_entry()
        for label, code in cls._LANGUAGES:
            cls._lang_entry.append(code, f"{label}" + (f" ({code})" if code else ""))
        # Select the row matching the current code, else put the raw code in.
        if not cls._lang_entry.set_active_id(CFG.WHISPER_LANGUAGE):
            cls._lang_entry.get_child().set_text(CFG.WHISPER_LANGUAGE)
        grid.attach(cls._lang_entry, 1, 2, 1, 1)

        vbox.pack_start(grid, False, False, 0)

        # whisper.cpp availability indicator (the "is it downloaded?" check) +
        # a download button.
        whisper_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cls._whisper_status = Gtk.Label()
        cls._whisper_status.set_halign(Gtk.Align.START)
        cls._whisper_status.set_line_wrap(True)
        dl_btn = Gtk.Button(label="Install / download")
        dl_btn.connect("clicked", cls._on_download_whispercpp)
        whisper_row.pack_start(cls._whisper_status, True, True, 0)
        whisper_row.pack_start(dl_btn, False, False, 0)
        vbox.pack_start(whisper_row, False, False, 0)

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

    @staticmethod
    def _combo_text(combo: Gtk.ComboBoxText) -> str:
        """Current text of an editable ComboBoxText (typed or selected)."""
        return combo.get_child().get_text().strip()

    @classmethod
    def _sync_model_entry(cls) -> None:
        """Repopulate the model combo for the selected backend + update hints."""
        bid, label, is_stream, model_key, cfg_attr = cls._selected_backend()
        combo = cls._model_entry
        combo.remove_all()
        if cfg_attr is None:  # auto — model resolved at runtime
            combo.get_child().set_text("")
            combo.set_sensitive(False)
            combo.get_child().set_placeholder_text("(chosen automatically)")
        else:
            combo.set_sensitive(True)
            for m in cls._MODELS.get(bid, []):
                combo.append_text(m)
            combo.get_child().set_text(getattr(CFG, cfg_attr))

        cap = "🔴 live transcription" if is_stream else "📝 batch (transcribing… indicator)"
        doc = cls._MODEL_DOCS.get(bid)
        hint = cap + (f" · models: {doc}" if doc else "")
        cls._set_trans_status(hint)
        cls._refresh_whisper_status()

    @classmethod
    def _refresh_whisper_status(cls) -> None:
        """Show whether local whisper.cpp is installed and the model downloaded."""
        if cls._whisper_status is None:
            return
        from linuxwhisper.transcription.whispercpp_backend import WhisperCppBackend
        model = CFG.WHISPERCPP_MODEL
        installed, downloaded = WhisperCppBackend(model).local_status()
        if not installed:
            msg = "whisper.cpp: ⚪ not installed — <tt>pip install -e '.[local]'</tt>"
        elif downloaded:
            msg = f"whisper.cpp: ✅ model <b>{model}</b> downloaded (offline ready)"
        else:
            msg = f"whisper.cpp: ⬇ model <b>{model}</b> not downloaded yet"
        cls._whisper_status.set_markup(f"<small>{msg}</small>")

    @classmethod
    def _on_download_whispercpp(cls, btn: Gtk.Button) -> None:
        """
        Set up local whisper.cpp entirely from the UI: pip-install the package
        if it's missing, then download + load the model — all in the background.
        """
        import threading
        btn.set_sensitive(False)
        threading.Thread(target=cls._setup_whispercpp_worker, args=(btn,), daemon=True).start()

    @classmethod
    def _setup_whispercpp_worker(cls, btn: Gtk.Button) -> None:
        import importlib
        import subprocess
        import sys
        from gi.repository import GLib

        from linuxwhisper.transcription.whispercpp_backend import WhisperCppBackend

        def status(markup: str) -> None:
            GLib.idle_add(lambda: cls._whisper_status.set_markup(f"<small>{markup}</small>"))

        model = CFG.WHISPERCPP_MODEL
        backend = WhisperCppBackend(model)

        # 1) install pywhispercpp if absent
        if not backend.is_available():
            status("whisper.cpp: ⏳ installing pywhispercpp…")
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "pywhispercpp"],
                    capture_output=True, text=True, timeout=900,
                )
            except Exception as e:
                status(f"whisper.cpp: ❌ install failed: {e}")
                GLib.idle_add(lambda: btn.set_sensitive(True))
                return
            importlib.invalidate_caches()
            if proc.returncode != 0 or not backend.is_available():
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                why = tail[-1] if tail else "see logs"
                status(f"whisper.cpp: ❌ install failed — {why}")
                GLib.idle_add(lambda: btn.set_sensitive(True))
                return

        # 2) download + load the model
        status(f"whisper.cpp: ⏳ downloading model <b>{model}</b>…")
        try:
            backend.prewarm()
        except Exception as e:
            status(f"whisper.cpp: ❌ download failed: {e}")
            GLib.idle_add(lambda: btn.set_sensitive(True))
            return

        GLib.idle_add(cls._refresh_whisper_status)
        GLib.idle_add(lambda: btn.set_sensitive(True))

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
        language = cls._resolve_language()
        fallback = "whispercpp" if cls._fallback_check.get_active() else ""

        values = {"backend": bid, "language": language, "fallback": fallback}
        if model_key is not None:
            model = cls._combo_text(cls._model_entry)
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

        # 3) report — and auto-install a missing streaming package if that's why.
        cls._report_backend_availability(bid)
        cls._refresh_whisper_status()
        print(f"⚙️ Transcription settings applied: {values}")

    # backend id -> (import module, pip package, env key, human name)
    _BACKEND_PKG = {
        "deepgram":        ("deepgram", "deepgram-sdk", "DEEPGRAM_API_KEY", "Deepgram"),
        "openai_realtime": ("openai",   "openai",       "OPENAI_API_KEY",   "OpenAI"),
    }

    @staticmethod
    def _module_installed(module: str) -> bool:
        import importlib.util
        return importlib.util.find_spec(module) is not None

    @classmethod
    def _report_backend_availability(cls, bid: str) -> None:
        """Status after Apply; auto-install a missing streaming package."""
        from linuxwhisper.transcription import get_dispatcher
        active = get_dispatcher().active
        if active is None:
            cls._set_trans_status("⚠️ Saved. No usable backend — fallback will be used.")
            return
        if active.is_available():
            cls._set_trans_status(f"✓ Applied live — backend <b>{active.name}</b>.")
            return

        info = cls._BACKEND_PKG.get(bid)
        if info:
            module, pip_name, env_key, human = info
            if not cls._module_installed(module):
                cls._install_backend_pkg_async(bid, pip_name, human)
                return
            import os
            if not os.environ.get(env_key):
                cls._set_trans_status(
                    f"⚠️ {human} needs its API key — set <b>{env_key}</b> in "
                    "“API Keys” below, then Apply. (Offline fallback used meanwhile.)"
                )
                return
        if bid in ("whispercpp", "auto"):
            cls._set_trans_status(
                "⚠️ Saved. whisper.cpp isn’t ready — use “Install / download” above."
            )
            return
        cls._set_trans_status(
            f"⚠️ Saved, but <b>{active.name}</b> is unavailable — fallback will be used."
        )

    @classmethod
    def _install_backend_pkg_async(cls, bid: str, pip_name: str, human: str) -> None:
        """pip-install a streaming backend's package, then re-apply."""
        import threading
        cls._set_trans_status(f"⏳ Installing {human} ({pip_name})…")

        def work():
            from gi.repository import GLib
            ok, msg = cls._pip_install(pip_name)

            def done():
                if not ok:
                    cls._set_trans_status(f"❌ {human} install failed — {msg}")
                    return False
                # Rebuild backends now that the package exists, then re-report.
                from linuxwhisper import config as config_module
                from linuxwhisper.transcription import reconfigure_dispatcher
                reconfigure_dispatcher(config_module.reload_config())
                cls._report_backend_availability(bid)
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _pip_install(pip_name: str):
        """Run pip install in-process venv. Returns (ok, last_output_line)."""
        import importlib
        import subprocess
        import sys
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_name],
                capture_output=True, text=True, timeout=900,
            )
        except Exception as e:
            return False, str(e)
        importlib.invalidate_caches()
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return False, (tail[-1] if tail else "see logs")
        return True, "ok"

    @classmethod
    def _resolve_language(cls) -> str:
        """Map the language combo's text/selection to an ISO-639-1 code."""
        # If a known row is selected, its id IS the code.
        active_id = cls._lang_entry.get_active_id()
        if active_id is not None:
            return active_id
        # Otherwise parse free text: accept "fr", "French", or "French (fr)".
        text = cls._combo_text(cls._lang_entry)
        if not text or text.lower() in ("autodetect", "auto"):
            return ""
        if "(" in text and text.endswith(")"):
            return text[text.rfind("(") + 1:-1].strip()
        for lbl, code in cls._LANGUAGES:
            if text.lower() == lbl.lower():
                return code
        return text  # assume the user typed a raw code

    # -----------------------------------------------------------------
    # Post-processing section (correct / reformulate / translate)
    # -----------------------------------------------------------------
    @classmethod
    def _build_postprocess_section(cls, vbox: Gtk.Box) -> None:
        header = Gtk.Label()
        header.set_halign(Gtk.Align.START)
        header.set_markup("<b>Post-processing</b> <small>(dictation → LLM)</small>")
        vbox.pack_start(header, False, False, 6)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)

        grid.attach(cls._row_label("Mode:"), 0, 0, 1, 1)
        cls._pp_combo = Gtk.ComboBoxText()
        current = (CFG.POSTPROCESS_MODE or "none").strip().lower()
        active_idx = 0
        for i, (mid, label) in enumerate(cls._POSTPROCESS_MODES):
            cls._pp_combo.append_text(label)
            if mid == current:
                active_idx = i
        cls._pp_combo.set_active(active_idx)
        cls._pp_combo.connect("changed", cls._on_pp_mode_changed)
        grid.attach(cls._pp_combo, 1, 0, 1, 1)

        cls._pp_lang_label = cls._row_label("Translate to:")
        grid.attach(cls._pp_lang_label, 0, 1, 1, 1)
        cls._pp_lang = Gtk.ComboBoxText.new_with_entry()
        for label, code in cls._LANGUAGES:
            if code:  # skip "Autodetect" — translation needs a real target
                cls._pp_lang.append(code, f"{label} ({code})")
        if not cls._pp_lang.set_active_id(CFG.POSTPROCESS_TARGET_LANG):
            cls._pp_lang.get_child().set_text(CFG.POSTPROCESS_TARGET_LANG)
        grid.attach(cls._pp_lang, 1, 1, 1, 1)
        vbox.pack_start(grid, False, False, 0)
        # "Translate to" only matters for the Translate mode — grey it out
        # otherwise so it's clear the other modes keep the original language.
        cls._update_pp_lang_sensitivity()

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.connect("clicked", cls._on_apply_postprocess)
        row.pack_start(apply_btn, False, False, 0)
        cls._pp_status = Gtk.Label()
        cls._pp_status.set_halign(Gtk.Align.START)
        cls._pp_status.set_line_wrap(True)
        row.pack_start(cls._pp_status, True, True, 0)
        vbox.pack_start(row, False, False, 0)

        hint = Gtk.Label()
        hint.set_halign(Gtk.Align.START)
        hint.set_markup(
            "<small><i>Applied to dictation before it's typed. Uses the Groq chat "
            "model (needs GROQ_API_KEY). Off adds no latency.</i></small>"
        )
        vbox.pack_start(hint, False, False, 0)

    @classmethod
    def _selected_pp_mode(cls) -> str:
        idx = cls._pp_combo.get_active() if cls._pp_combo else 0
        return cls._POSTPROCESS_MODES[idx][0] if 0 <= idx < len(cls._POSTPROCESS_MODES) else "none"

    @classmethod
    def _update_pp_lang_sensitivity(cls) -> None:
        """Enable 'Translate to' only for the Translate mode."""
        is_translate = cls._selected_pp_mode() == "translate"
        if cls._pp_lang:
            cls._pp_lang.set_sensitive(is_translate)
        if cls._pp_lang_label:
            cls._pp_lang_label.set_sensitive(is_translate)

    @classmethod
    def _on_pp_mode_changed(cls, _combo: Gtk.ComboBoxText) -> None:
        cls._update_pp_lang_sensitivity()

    @classmethod
    def _on_apply_postprocess(cls, _btn: Gtk.Button) -> None:
        mode = cls._selected_pp_mode()
        lang = cls._pp_lang.get_active_id() or cls._combo_text(cls._pp_lang)
        # If a "Name (code)" row was typed, extract the code.
        if "(" in lang and lang.endswith(")"):
            lang = lang[lang.rfind("(") + 1:-1].strip()

        from linuxwhisper.config_io import ConfigWriteError, update_section
        try:
            update_section("postprocess", {"mode": mode, "target_language": lang})
        except ConfigWriteError as e:
            cls._pp_status.set_markup(f"<small>❌ {e}</small>")
            return
        from linuxwhisper import config as config_module
        config_module.reload_config()  # PostProcessor reads config_module.CFG live
        desc = "off" if mode == "none" else (f"translate → {lang}" if mode == "translate" else mode)
        cls._pp_status.set_markup(f"<small>✓ Applied live — {desc}.</small>")
        print(f"✨ Post-processing set: mode={mode} lang={lang}")

    # -----------------------------------------------------------------
    # API keys section (#stored in secrets.env, applied live)
    # -----------------------------------------------------------------
    @classmethod
    def _build_api_keys_section(cls, vbox: Gtk.Box) -> None:
        import os
        from linuxwhisper.secrets import MANAGED_KEYS, SECRETS_FILE, read_secrets

        header = Gtk.Label()
        header.set_halign(Gtk.Align.START)
        header.set_markup("<b>API Keys</b>")
        vbox.pack_start(header, False, False, 6)

        stored = read_secrets()
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)
        cls._key_entries = {}

        for i, (key, label) in enumerate(MANAGED_KEYS.items()):
            lbl = Gtk.Label(label=label + ":")
            lbl.set_halign(Gtk.Align.START)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_visibility(False)  # masked
            entry.set_text(stored.get(key, ""))
            # If a key is active via the inherited env (e.g. environment.d) but
            # not stored here, hint that without exposing it.
            if not stored.get(key) and os.environ.get(key):
                entry.set_placeholder_text("(set via environment — type to override)")
            grid.attach(lbl, 0, i, 1, 1)
            grid.attach(entry, 1, i, 1, 1)
            cls._key_entries[key] = entry

        vbox.pack_start(grid, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        save_btn = Gtk.Button(label="Save keys")
        save_btn.connect("clicked", cls._on_save_keys)
        controls.pack_start(save_btn, False, False, 0)
        show_chk = Gtk.CheckButton(label="Show")
        show_chk.connect("toggled", cls._on_toggle_key_visibility)
        controls.pack_start(show_chk, False, False, 0)
        cls._key_status = Gtk.Label()
        cls._key_status.set_halign(Gtk.Align.START)
        cls._key_status.set_line_wrap(True)
        controls.pack_start(cls._key_status, True, True, 0)
        vbox.pack_start(controls, False, False, 0)

        hint = Gtk.Label()
        hint.set_halign(Gtk.Align.START)
        hint.set_markup(
            f"<small><i>Stored in <tt>{SECRETS_FILE}</tt> (chmod 600), loaded at "
            "startup — persists across reboot. Applied live on save.</i></small>"
        )
        vbox.pack_start(hint, False, False, 0)

    @classmethod
    def _on_toggle_key_visibility(cls, chk: Gtk.CheckButton) -> None:
        for entry in (cls._key_entries or {}).values():
            entry.set_visibility(chk.get_active())

    @classmethod
    def _on_save_keys(cls, _btn: Gtk.Button) -> None:
        from linuxwhisper.secrets import save_secrets
        values = {k: e.get_text() for k, e in (cls._key_entries or {}).items()}
        try:
            save_secrets(values)
        except OSError as e:
            cls._key_status.set_markup(f"<small>❌ {e}</small>")
            return
        # Re-evaluate backends now that keys changed.
        from linuxwhisper import config as config_module
        from linuxwhisper.transcription import reconfigure_dispatcher
        reconfigure_dispatcher(config_module.reload_config())
        n = sum(1 for v in values.values() if v.strip())
        cls._key_status.set_markup(
            f"<small>✓ Saved {n} key(s) (chmod 600) &amp; applied live.</small>"
        )
        # Refresh the transcription status hint if it's showing an availability warning.
        if cls._backend_combo is not None:
            cls._sync_model_entry()
        print("🔑 API keys saved & applied.")

    # -----------------------------------------------------------------
    # Hotkeys section (editable)
    # -----------------------------------------------------------------
    _HOTKEY_LABELS = {
        "dictation": "Dictation", "ai": "AI Chat", "ai_rewrite": "Rewrite",
        "vision": "Vision", "pin": "Pin Chat", "tts": "TTS Toggle",
    }

    @classmethod
    def _build_hotkeys_section(cls, vbox: Gtk.Box) -> None:
        """Editable per-mode key bindings (evdev key names), applied on restart."""
        header = Gtk.Label()
        header.set_halign(Gtk.Align.START)
        header.set_markup("<b>Hotkeys</b>")
        vbox.pack_start(header, False, False, 8)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(6)
        cls._hotkey_entries = {}
        cls._hotkey_capture_btns = []

        for i, (mode_id, (_label, primary, extras)) in enumerate(CFG.HOTKEY_DEFS.items()):
            name = cls._HOTKEY_LABELS.get(mode_id, mode_id.replace("_", " ").title())
            lbl = Gtk.Label(label=name + ":")
            lbl.set_halign(Gtk.Align.START)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            names = [cls._keycode_to_name(c) for c in [primary, *extras]]
            entry.set_text(" ".join(n for n in names if n))

            capture_btn = Gtk.Button(label="⌨ Set")
            capture_btn.set_tooltip_text("Press a key to bind it (no need to know its name)")
            capture_btn.connect("clicked", cls._on_capture_hotkey, mode_id, entry)

            grid.attach(lbl, 0, i, 1, 1)
            grid.attach(entry, 1, i, 1, 1)
            grid.attach(capture_btn, 2, i, 1, 1)
            cls._hotkey_entries[mode_id] = entry
            cls._hotkey_capture_btns.append(capture_btn)

        vbox.pack_start(grid, False, False, 0)

        hint = Gtk.Label()
        hint.set_halign(Gtk.Align.START)
        hint.set_markup(
            "<small><i>Click <b>⌨ Set</b> then press the key — or type space-separated "
            "evdev names (first = primary, rest = aliases). Applied after a service restart.</i></small>"
        )
        vbox.pack_start(hint, False, False, 0)

        apply_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        apply_btn = Gtk.Button(label="Apply hotkeys")
        apply_btn.connect("clicked", cls._on_apply_hotkeys)
        apply_row.pack_start(apply_btn, False, False, 0)
        cls._hotkey_status = Gtk.Label()
        cls._hotkey_status.set_halign(Gtk.Align.START)
        cls._hotkey_status.set_line_wrap(True)
        apply_row.pack_start(cls._hotkey_status, True, True, 0)
        vbox.pack_start(apply_row, False, False, 0)

    @staticmethod
    def _keycode_to_name(code: int) -> str:
        """Reverse an evdev keycode to a clean key name (e.g. 'RIGHTALT')."""
        from evdev import ecodes
        name = ecodes.KEY.get(code)
        if isinstance(name, (list, tuple)):
            name = name[0]
        return name.replace("KEY_", "") if name else str(code)

    @classmethod
    def _on_apply_hotkeys(cls, _btn: Gtk.Button) -> None:
        """Validate every binding, then write [hotkeys] to config.toml."""
        from linuxwhisper.config import _resolve_keycode

        parsed: dict = {}
        for mode_id, entry in cls._hotkey_entries.items():
            raw = entry.get_text().replace(",", " ").split()
            if not raw:
                cls._set_hotkey_status(f"❌ {mode_id}: at least one key is required.")
                return
            for n in raw:  # validate now so we never write a broken binding
                try:
                    _resolve_keycode(n)
                except ValueError:
                    cls._set_hotkey_status(f"❌ unknown key '{n}' for {mode_id}.")
                    return
            parsed[mode_id] = [n.strip().upper().replace("KEY_", "") for n in raw]

        from linuxwhisper.config_io import ConfigWriteError, update_section
        try:
            update_section("hotkeys", parsed)
        except ConfigWriteError as e:
            cls._set_hotkey_status(f"❌ {e}")
            return

        cls._set_hotkey_status(
            "✓ Saved. Restart to apply: "
            "<tt>systemctl --user restart app-linuxwhisper@autostart.service</tt>"
        )
        print(f"⌨️  Hotkeys saved: {parsed}")

    @classmethod
    def _set_hotkey_status(cls, markup: str) -> None:
        if cls._hotkey_status:
            cls._hotkey_status.set_markup(f"<small>{markup}</small>")

    @classmethod
    def _set_capture_enabled(cls, enabled: bool) -> None:
        for b in (cls._hotkey_capture_btns or []):
            b.set_sensitive(enabled)

    @classmethod
    def _on_capture_hotkey(cls, _btn: Gtk.Button, mode_id: str, entry: Gtk.Entry) -> None:
        """Capture the next key press and put its evdev name in the entry."""
        import threading
        label = cls._HOTKEY_LABELS.get(mode_id, mode_id)
        cls._set_capture_enabled(False)
        cls._set_hotkey_status(f"⌨ Press a key for <b>{label}</b>… (6s)")

        def work():
            from gi.repository import GLib
            from linuxwhisper.handlers.keyboard import KeyboardHandler
            name = KeyboardHandler.capture_next_key()

            def done():
                if name:
                    entry.set_text(name)
                    cls._set_hotkey_status(
                        f"✓ Captured <b>{name}</b> for {label} — click <b>Apply hotkeys</b> to save."
                    )
                else:
                    cls._set_hotkey_status("⚠️ No key captured (timed out or no input device).")
                cls._set_capture_enabled(True)
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _on_voice_changed(combo: Gtk.ComboBoxText) -> None:
        """Handle voice selection change."""
        voice = combo.get_active_text().lower()
        STATE.tts_voice = voice
        print(f"🎙️ Voice changed to: {voice}")
        SettingsManager.save(STATE)

    @classmethod
    def _on_scheme_selected(cls, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
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
            # Refresh the overlay preview to reflect the new theme.
            if cls._preview_area:
                cls._preview_area.queue_draw()
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
