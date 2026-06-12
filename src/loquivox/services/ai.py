"""
AI chat and vision completion service.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loquivox.api import get_client
from loquivox.config import CFG
from loquivox.decorators import safe_execute
from loquivox.state import STATE


class AIService:
    """AI chat and vision completion service."""

    @staticmethod
    def build_messages(user_content: str) -> List[Dict[str, Any]]:
        """Build API messages with system prompt and conversation history."""
        messages = [{"role": "system", "content": CFG.SYSTEM_PROMPT}]
        messages.extend(STATE.conversation_history)
        messages.append({"role": "user", "content": user_content})
        return messages

    @staticmethod
    @safe_execute("AI Chat")
    def chat(prompt: str) -> Optional[str]:
        """Send chat completion request."""
        messages = AIService.build_messages(prompt)
        response = get_client().chat.completions.create(
            model=CFG.MODEL_CHAT,
            messages=messages
        )
        return response.choices[0].message.content

    @staticmethod
    @safe_execute("AI Vision")
    def vision(prompt: str, image_base64: str) -> Optional[str]:
        """Send vision completion request with image."""
        messages = AIService.build_messages(prompt)
        # Replace last user message with multimodal content
        messages[-1] = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
            ]
        }
        response = get_client().chat.completions.create(
            model=CFG.MODEL_VISION,
            messages=messages
        )
        return response.choices[0].message.content
