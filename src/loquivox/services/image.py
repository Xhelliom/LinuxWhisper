"""
Screenshot and image encoding service.

Uses the platform abstraction layer to work on both X11 and Wayland.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from loquivox.config import CFG
from loquivox.decorators import safe_execute
from loquivox.platform import get_screenshot


class ImageService:
    """Screenshot and image encoding service."""

    @staticmethod
    @safe_execute("Screenshot")
    def take_screenshot() -> Optional[str]:
        """Take screenshot and return base64 encoded string."""
        screenshot = get_screenshot()
        success = screenshot.take_screenshot(CFG.TEMP_SCREEN_PATH)
        if not success:
            print("❌ Screenshot failed")
            return None

        with open(CFG.TEMP_SCREEN_PATH, "rb") as f:
            encoded = base64.b64encode(f.read()).decode('utf-8')
        os.remove(CFG.TEMP_SCREEN_PATH)
        return encoded
