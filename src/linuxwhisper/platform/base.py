"""
Abstract base classes for platform backends.

Each backend (X11, Wayland) must implement these interfaces.
Adding a new platform (e.g. macOS) requires only a new module
that provides concrete implementations of these ABCs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ClipboardBackend(ABC):
    """Platform-specific clipboard operations."""

    @abstractmethod
    def copy(self, text: str) -> None:
        """Copy text to the system clipboard."""

    @abstractmethod
    def paste(self) -> str:
        """Return the current clipboard contents as text."""


class InputBackend(ABC):
    """Platform-specific keyboard/input simulation."""

    @abstractmethod
    def simulate_paste(self, is_terminal: bool = False) -> None:
        """
        Simulate a paste keystroke (Ctrl+V or Ctrl+Shift+V for terminals).
        """

    @abstractmethod
    def simulate_copy(self, is_terminal: bool = False) -> None:
        """
        Simulate a copy keystroke (Ctrl+C or Ctrl+Shift+C for terminals).
        """

    @abstractmethod
    def is_terminal_focused(self) -> bool:
        """
        Check if the currently focused window is a terminal emulator.

        Used to decide between Ctrl+V and Ctrl+Shift+V style shortcuts.
        Returns False if detection is not possible (safe default).
        """


class ScreenshotBackend(ABC):
    """Platform-specific screenshot capture."""

    @abstractmethod
    def take_screenshot(self, output_path: str) -> bool:
        """
        Capture a full-screen screenshot and save to *output_path*.

        Returns:
            True on success, False on failure.
        """
