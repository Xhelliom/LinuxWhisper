"""
Global keyboard listener using evdev.

Reads key events directly from /dev/input/ devices, which works on both
X11 and Wayland without any display server integration. Requires the
user to be in the 'input' group.
"""
from __future__ import annotations

import logging
import selectors
from typing import Any, Dict, List, Optional

import evdev
from evdev import InputDevice, categorize, ecodes

from loquivox.config import CFG
from loquivox.handlers.mode import ModeHandler
from loquivox.managers.chat import ChatManager
from loquivox.managers.overlay import OverlayManager
from loquivox.services.audio import AudioService
from loquivox.services.clipboard import ClipboardService
from loquivox.services.tts import TTSService
from loquivox.state import STATE

logger = logging.getLogger(__name__)


class KeyboardHandler:
    """Global keyboard listener using evdev (works on X11 + Wayland)."""

    # Flat lookup keycode -> mode_id for all recording modes + toggle actions.
    # Rebuilt by reload_hotkeys() so edits in the settings UI apply live
    # (the listener reads this dict on every key event — no restart needed).
    _KEY_TO_MODE: Dict[int, str] = {}

    @classmethod
    def reload_hotkeys(cls, config: Any = None) -> None:
        """
        Rebuild the keycode→mode map from ``config`` (or the live ``CFG``).

        Called once at import time and again by the settings UI after the
        user edits hotkeys, so new bindings take effect immediately without
        restarting the service. A brand-new dict is assigned (never mutated
        in place) so the listener thread always reads a consistent map.
        """
        cfg = config if config is not None else CFG
        mapping: Dict[int, str] = {}
        for mode_id, (_, primary, extras) in cfg.HOTKEY_DEFS.items():
            mapping[primary] = mode_id
            for extra in extras:
                mapping[extra] = mode_id
        cls._KEY_TO_MODE = mapping

    @staticmethod
    def _is_keyboard(dev: InputDevice) -> bool:
        """Heuristic: a device with EV_KEY exposing typical keyboard keys."""
        try:
            caps = dev.capabilities()
        except Exception:
            return False
        if ecodes.EV_KEY not in caps:
            return False
        key_caps = caps[ecodes.EV_KEY]
        # Require some function/letter keys to filter out mice, lid switches, etc.
        return ecodes.KEY_F1 in key_caps or ecodes.KEY_A in key_caps

    @classmethod
    def _find_keyboards(cls) -> List[InputDevice]:
        """Discover all keyboard input devices (opens a fresh handle each)."""
        keyboards = []
        for path in evdev.list_devices():
            try:
                dev = InputDevice(path)
            except Exception:
                continue
            if cls._is_keyboard(dev):
                keyboards.append(dev)
                logger.debug("Found keyboard: %s (%s)", dev.name, dev.path)
            else:
                try:
                    dev.close()
                except Exception:
                    pass

        if not keyboards:
            logger.warning(
                "No keyboard devices found! "
                "Make sure you are in the 'input' group: "
                "sudo usermod -aG input $USER"
            )
        return keyboards

    @staticmethod
    def keycode_to_name(code: int) -> Optional[str]:
        """Reverse an evdev keycode to a clean key name (e.g. 'HOME'), or None."""
        name = ecodes.KEY.get(code)
        if isinstance(name, (list, tuple)):
            name = name[0]
        return name.replace("KEY_", "") if name else None

    @classmethod
    def capture_next_key(cls, timeout: float = 6.0) -> Optional[str]:
        """
        Block until the next key is pressed on any keyboard and return its evdev
        name (e.g. 'HOME', 'RIGHTALT'), or None on timeout / no device.

        Used by the settings UI's "capture" button so the user can press a key
        instead of guessing its evdev name. Devices are grabbed for the (short)
        duration so the keypress doesn't leak to the focused app or trigger an
        existing hotkey. Meant to run in a background thread; always ungrabs.
        """
        import time

        devices = cls._find_keyboards()
        if not devices:
            return None

        sel = selectors.DefaultSelector()
        grabbed: List[InputDevice] = []
        for dev in devices:
            try:
                sel.register(dev, selectors.EVENT_READ)
            except Exception:
                continue
            try:
                dev.grab()
                grabbed.append(dev)
            except Exception:
                pass  # grab is best-effort; capture still works passively

        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                for key_obj, _ in sel.select(timeout=remaining):
                    try:
                        events = list(key_obj.fileobj.read())
                    except Exception:
                        continue
                    for event in events:
                        # First key-DOWN of a real KEY_* code wins.
                        if event.type == ecodes.EV_KEY and event.value == 1:
                            name = cls.keycode_to_name(event.code)
                            if name:
                                return name
        finally:
            for dev in grabbed:
                try:
                    dev.ungrab()
                except Exception:
                    pass
            for dev in devices:
                try:
                    sel.unregister(dev)
                except Exception:
                    pass
                try:
                    dev.close()
                except Exception:
                    pass

    @classmethod
    def _get_mode_for_keycode(cls, keycode: int) -> Optional[str]:
        """Get mode name for a keycode, if any."""
        return cls._KEY_TO_MODE.get(keycode)

    @classmethod
    def _is_recording_mode(cls, mode: str) -> bool:
        """Check if a mode triggers audio recording."""
        return mode in CFG.MODES

    @classmethod
    def _handle_key_event(cls, event: evdev.InputEvent) -> None:
        """Process a single key event."""
        key_event = categorize(event)
        keycode = event.code

        mode = cls._get_mode_for_keycode(keycode)
        if mode is None:
            return

        # Key DOWN
        if key_event.keystate == key_event.key_down:
            cls._on_press(mode)

        # Key UP
        elif key_event.keystate == key_event.key_up:
            cls._on_release(mode)

    # Hotkeys that act on the session itself rather than starting a recording.
    _NON_RECORDING_ACTIONS = ("pin", "tts", "cancel", "pause")

    @classmethod
    def _on_press(cls, mode: str) -> None:
        """Handle key press for a recognized mode."""
        # Cancel the active recording / in-flight transcription (no insert).
        if mode == "cancel":
            ModeHandler.cancel_active()
            return

        # Pause / resume the current recording (only while one is active).
        if mode == "pause":
            if STATE.recording:
                cls._toggle_pause()
            return

        # Pin toggle (non-recording action)
        if mode == "pin":
            if not STATE.recording:
                ChatManager.toggle_pin()
            return

        # TTS toggle (non-recording action)
        if mode == "tts":
            if not STATE.recording:
                TTSService.toggle()
            return

        # Toggle mode: pressing same key again stops recording
        if STATE.recording and STATE.toggle_mode:
            if mode == STATE.current_mode:
                cls._stop_and_process()
            return

        if STATE.recording:
            return

        # Start recording for this mode
        if cls._is_recording_mode(mode):
            STATE.current_mode = mode

            # For rewrite mode, copy selected text first
            if mode == "ai_rewrite":
                ClipboardService.copy_selected()

            OverlayManager.show(mode)
            AudioService.start_recording()

    @classmethod
    def _toggle_pause(cls) -> None:
        """Flip the paused state of the active recording and reflect it on the overlay."""
        STATE.paused = not STATE.paused
        OverlayManager.set_paused(STATE.paused)
        print("⏸️  Paused" if STATE.paused else "▶️  Resumed")

    @classmethod
    def _on_release(cls, mode: str) -> None:
        """Handle key release for a recognized mode."""
        # Session-action keys only act on press; their release is a no-op
        # (and must not stop a recording that is still in progress, e.g. paused).
        if mode in cls._NON_RECORDING_ACTIONS:
            return

        if not STATE.recording:
            return

        # In toggle mode, release does nothing
        if STATE.toggle_mode:
            return

        # Hold mode: release key stops recording
        if mode == STATE.current_mode:
            cls._stop_and_process()

    @classmethod
    def _stop_and_process(cls) -> None:
        """
        Stop recording and hand transcription off-thread.

        Runs on the keyboard listener thread, so it must not block on the
        network or touch GTK directly: the overlay hide is already marshalled
        to the main loop, stop_recording() is not a GTK call, and
        transcription + processing run in a worker thread.
        """
        OverlayManager.set_transcribing()
        audio_data = AudioService.stop_recording()

        # Route the live session (if any) or the buffered audio, like the
        # silence-stop path in ModeHandler.stop_recording_safe.
        session = STATE.stream_session
        STATE.stream_session = None

        if session is not None:
            ModeHandler.process_stream_async(STATE.current_mode, session, audio_data)
        elif audio_data is not None:
            ModeHandler.process_audio_async(STATE.current_mode, audio_data)
        else:
            OverlayManager.hide()

    # Re-scan interval (seconds) to pick up keyboards that (re)appear, e.g.
    # after resume from suspend or USB hotplug.
    _RESCAN_INTERVAL_SEC: float = 3.0
    _stop: bool = False

    @classmethod
    def stop(cls) -> None:
        """Request the listener loop to exit (the daemon thread will end)."""
        cls._stop = True

    @classmethod
    def _sync_devices(
        cls,
        sel: "selectors.BaseSelector",
        registered: Dict[str, InputDevice],
    ) -> None:
        """
        Register any keyboards not already tracked.

        Compares by device path so existing handles are never reopened
        (avoids fd leaks). Vanished devices are pruned lazily on read
        failure in the main loop, since list_devices() may briefly omit a
        device that is still readable.
        """
        for path in evdev.list_devices():
            if path in registered:
                continue
            try:
                dev = InputDevice(path)
            except Exception:
                continue
            if not cls._is_keyboard(dev):
                try:
                    dev.close()
                except Exception:
                    pass
                continue
            try:
                sel.register(dev, selectors.EVENT_READ)
                registered[path] = dev
                logger.info("Registered keyboard: %s (%s)", dev.name, path)
            except Exception:
                try:
                    dev.close()
                except Exception:
                    pass

    @classmethod
    def _drop_device(
        cls,
        sel: "selectors.BaseSelector",
        registered: Dict[str, InputDevice],
        device: InputDevice,
    ) -> None:
        """Unregister, close and forget a disconnected device."""
        logger.warning("Device disconnected: %s", device.path)
        try:
            sel.unregister(device)
        except Exception:
            pass
        registered.pop(device.path, None)
        try:
            device.close()
        except Exception:
            pass

    @classmethod
    def run(cls) -> None:
        """
        Start the evdev keyboard listener (blocking).

        Monitors all keyboard devices using a selector. Survives device
        disconnects (suspend/resume, hotplug): it never exits on its own,
        and re-scans every ``_RESCAN_INTERVAL_SEC`` to (re)register
        keyboards as they come back. Runs in a background daemon thread —
        started from app.py.
        """
        import time

        cls._stop = False
        sel = selectors.DefaultSelector()
        registered: Dict[str, InputDevice] = {}

        cls._sync_devices(sel, registered)
        if registered:
            print(f"⌨️  Listening on {len(registered)} keyboard device(s)")
        else:
            print(
                "⏳ No keyboard accessible yet — will keep scanning.\n"
                "   If this persists: sudo usermod -aG input $USER (then re-login)."
            )

        last_scan = time.monotonic()
        try:
            while not cls._stop:
                for key, _ in sel.select(timeout=cls._RESCAN_INTERVAL_SEC):
                    device = key.fileobj
                    try:
                        for event in device.read():
                            if event.type == ecodes.EV_KEY:
                                cls._handle_key_event(event)
                    except OSError:
                        # Device disconnected — drop it and keep going. It will
                        # be re-registered by the periodic re-scan when it
                        # reappears (new event number after resume).
                        cls._drop_device(sel, registered, device)

                now = time.monotonic()
                if now - last_scan >= cls._RESCAN_INTERVAL_SEC:
                    cls._sync_devices(sel, registered)
                    last_scan = now
        except Exception as e:
            logger.error("Keyboard listener error: %s", e)
        finally:
            for dev in list(registered.values()):
                try:
                    dev.close()
                except Exception:
                    pass
            sel.close()


# Populate the keycode→mode map from the config loaded at import time.
KeyboardHandler.reload_hotkeys()
