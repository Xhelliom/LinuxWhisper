"""
X11 platform backends.

Uses: xdotool, xprop, xclip, gnome-screenshot
"""
from __future__ import annotations

import subprocess
from typing import Tuple

from loquivox.platform.base import ClipboardBackend, InputBackend, ScreenshotBackend

# Substrings matched against WM_CLASS (lowercase) to detect terminals.
_TERMINAL_KEYWORDS: Tuple[str, ...] = (
    "terminal", "terminator", "tilix", "alacritty", "kitty",
    "konsole", "xterm", "urxvt", "sakura", "terminology",
    "guake", "tilda", "yakuake", "wezterm", "foot",
    "cool-retro-term", "hyper", "tabby", "rio", "ghostty",
)


class X11Clipboard(ClipboardBackend):
    """Clipboard via xclip (X11)."""

    def copy(self, text: str) -> None:
        try:
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard"],
                stdin=subprocess.PIPE,
            )
            proc.communicate(input=text.encode("utf-8"))
        except Exception as e:
            print(f"⚠️ X11 clipboard copy error: {e}")

    def paste(self) -> str:
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout
        except Exception:
            return ""


class X11Input(InputBackend):
    """Input simulation via xdotool (X11)."""

    def simulate_paste(self, is_terminal: bool = False) -> None:
        key = "ctrl+shift+v" if is_terminal else "ctrl+v"
        subprocess.run(["xdotool", "key", key], timeout=2)

    def simulate_copy(self, is_terminal: bool = False) -> None:
        key = "ctrl+shift+c" if is_terminal else "ctrl+c"
        subprocess.run(["xdotool", "key", key], timeout=2)

    def is_terminal_focused(self) -> bool:
        try:
            win_id = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=1,
            ).stdout.strip()
            if not win_id:
                return False
            result = subprocess.run(
                ["xprop", "-id", win_id, "WM_CLASS"],
                capture_output=True, text=True, timeout=1,
            )
            wm_class = result.stdout.strip().lower()
            return any(kw in wm_class for kw in _TERMINAL_KEYWORDS)
        except Exception:
            return False


class X11Screenshot(ScreenshotBackend):
    """Screenshot via gnome-screenshot (X11/GNOME)."""

    def take_screenshot(self, output_path: str) -> bool:
        try:
            result = subprocess.run(
                ["gnome-screenshot", "-f", output_path],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False
