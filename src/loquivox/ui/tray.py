"""
System tray (AppIndicator) management.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Dict

from loquivox.config import CFG
from loquivox.decorators import run_on_main_thread
from loquivox.state import STATE, HAS_APP_INDICATOR

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

if HAS_APP_INDICATOR:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as AppIndicator


class TrayManager:
    """System tray (AppIndicator) management."""

    @staticmethod
    def start() -> None:
        """Initialize and start system tray."""
        if not HAS_APP_INDICATOR:
            print("⚠️ AyatanaAppIndicator3 not available — running without tray icon.")
            print("   Install: libayatana-appindicator (Arch) or gir1.2-ayatanaappindicator3-0.1 (Debian)")
            Gtk.main()
            return

        STATE.indicator = AppIndicator.Indicator.new(
            "loquivox",
            "emblem-favorite",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        STATE.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        STATE.indicator.set_title("Loquivox")
        TrayManager.update_menu()
        Gtk.main()

    @staticmethod
    @run_on_main_thread
    def update_menu() -> None:
        """Rebuild and update tray menu."""
        if not STATE.indicator:
            return
        STATE.gtk_menu = TrayManager._build_menu()
        STATE.indicator.set_menu(STATE.gtk_menu)

    @staticmethod
    def _build_menu() -> Gtk.Menu:
        """Build GTK menu for tray."""
        # Late imports to avoid circular dependencies
        from loquivox.managers.history import HistoryManager
        from loquivox.services.clipboard import ClipboardService
        from loquivox.ui.settings_dialog import SettingsDialog

        menu = Gtk.Menu()

        # History items
        if STATE.answer_history:
            for item in STATE.answer_history[:CFG.ANSWER_HISTORY_LIMIT]:
                preview = item["text"][:50].replace("\n", " ")
                if len(item["text"]) > 50:
                    preview += "..."
                label = f"[{item['timestamp']}] {preview}"
                menu_item = Gtk.MenuItem(label=label)
                menu_item.connect("activate", TrayManager._make_history_callback(item, ClipboardService))
                menu.append(menu_item)
            menu.append(Gtk.SeparatorMenuItem())
        else:
            empty = Gtk.MenuItem(label="(No History)")
            empty.set_sensitive(False)
            menu.append(empty)
            menu.append(Gtk.SeparatorMenuItem())

        # Clear history
        clear = Gtk.MenuItem(label="Clear History")
        clear.connect("activate", lambda w: HistoryManager.clear_all())
        menu.append(clear)
        
        menu.append(Gtk.SeparatorMenuItem())
        
        # Chat toggle
        chat_toggle = Gtk.CheckMenuItem(label="Show Chat Overlay")
        chat_toggle.set_active(STATE.chat_enabled)
        chat_toggle.connect("toggled", TrayManager._toggle_chat)
        menu.append(chat_toggle)

        # Toggle mode (hold vs press-to-toggle)
        toggle_mode = Gtk.CheckMenuItem(label="Toggle Mode (Press to Record)")
        toggle_mode.set_active(STATE.toggle_mode)
        toggle_mode.connect("toggled", TrayManager._toggle_mode)
        menu.append(toggle_mode)

        # Post-processing submenu (quick toggle; fine-tune in Settings)
        menu.append(TrayManager._build_postprocess_item())

        # Settings
        settings_item = Gtk.MenuItem(label="Settings")
        settings_item.connect("activate", lambda w: SettingsDialog.show())
        menu.append(settings_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", TrayManager._quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    @staticmethod
    def _build_postprocess_item() -> Gtk.MenuItem:
        """A 'Post-processing' menu entry with a radio submenu of modes."""
        import loquivox.config as config_module
        from loquivox.ui.settings_dialog import SettingsDialog

        current = (config_module.CFG.POSTPROCESS_MODE or "none").strip().lower()
        modes = SettingsDialog._POSTPROCESS_MODES

        item = Gtk.MenuItem(label="Post-processing")
        submenu = Gtk.Menu()
        group = None
        for mid, label in modes:
            radio = Gtk.RadioMenuItem(label=label, group=group)
            group = radio
            radio.set_active(mid == current)
            radio.connect("toggled", TrayManager._on_postprocess_selected, mid)
            submenu.append(radio)
        item.set_submenu(submenu)
        return item

    @staticmethod
    def _on_postprocess_selected(widget, mode: str) -> None:
        """Apply a post-processing mode chosen from the tray (live + persisted)."""
        if not widget.get_active():
            return  # only the newly-selected radio acts
        from loquivox import config as config_module
        from loquivox.config_io import ConfigWriteError, update_section
        try:
            update_section("postprocess", {"mode": mode})
        except ConfigWriteError as e:
            print(f"⚠️ Could not save post-processing mode: {e}")
            return
        config_module.reload_config()  # PostProcessor reads config_module.CFG live
        print(f"✨ Post-processing mode → {mode}")

    @staticmethod
    def _make_history_callback(item: Dict[str, str], clipboard_service) -> Callable:
        """Create callback for history item click."""
        def callback(widget):
            # Remove prefix labels like [Dictation]
            clean = re.sub(r"^\[.*?\]\s*", "", item["text"])
            clipboard_service.paste_text(clean)
        return callback

    @staticmethod
    def _toggle_chat(widget) -> None:
        """Toggle chat overlay visibility."""
        STATE.chat_enabled = widget.get_active()
        from loquivox.state import SettingsManager
        SettingsManager.save(STATE)
        
        from loquivox.managers.chat import ChatManager
        if not STATE.chat_enabled:
            ChatManager._destroy()
        else:
            ChatManager.refresh_overlay()

    @staticmethod
    def _toggle_mode(widget) -> None:
        """Toggle between hold-to-record and press-to-toggle mode."""
        STATE.toggle_mode = widget.get_active()
        from loquivox.state import SettingsManager
        SettingsManager.save(STATE)

    @staticmethod
    def _quit(widget) -> None:
        """Quit application."""
        Gtk.main_quit()
        os._exit(0)
