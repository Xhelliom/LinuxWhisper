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

from linuxwhisper.config import CFG
from linuxwhisper.handlers.mode import ModeHandler
from linuxwhisper.managers.chat import ChatManager
from linuxwhisper.managers.overlay import OverlayManager
from linuxwhisper.services.audio import AudioService
from linuxwhisper.services.clipboard import ClipboardService
from linuxwhisper.services.tts import TTSService
from linuxwhisper.state import STATE

logger = logging.getLogger(__name__)


class KeyboardHandler:
    """Global keyboard listener using evdev (works on X11 + Wayland)."""

    # Build a flat lookup: keycode -> mode_id
    # for all recording modes + toggle actions
    _KEY_TO_MODE: Dict[int, str] = {}
    for mode_id, (_, primary, extras) in CFG.HOTKEY_DEFS.items():
        _KEY_TO_MODE[primary] = mode_id
        for extra in extras:
            _KEY_TO_MODE[extra] = mode_id

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

    @classmethod
    def _on_press(cls, mode: str) -> None:
        """Handle key press for a recognized mode."""
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
    def _on_release(cls, mode: str) -> None:
        """Handle key release for a recognized mode."""
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

        if audio_data is not None:
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
