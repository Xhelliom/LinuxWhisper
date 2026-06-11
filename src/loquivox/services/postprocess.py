"""
Optional post-processing of dictated text via the Groq LLM.

After transcription, the raw text can be cleaned up, reformulated, or translated
before it is typed. This is opt-in (default off) and runs off the GTK thread
(in the transcription worker), so it never freezes the UI. On any failure it
returns the original text unchanged.

Modes (``[postprocess] mode`` in config.toml):
  - ``none``        — pass through (default)
  - ``correct``     — fix spelling/grammar/punctuation, same language
  - ``reformulate`` — rewrite clearer/more fluent, same language
  - ``translate``   — translate into ``[postprocess] target_language``

Reads ``config.CFG`` through the module so settings changes apply live
(reload_config rebinds the module global).
"""
from __future__ import annotations

from typing import Optional

import loquivox.config as config_module
from loquivox.api import get_client
from loquivox.decorators import safe_execute

# Task system prompts. Each insists on returning ONLY the resulting text so the
# output can be typed verbatim.
_PROMPTS = {
    "correct": (
        "You fix dictated text: correct spelling, grammar, punctuation and "
        "capitalization. Keep the original language and meaning. Do not add or "
        "remove content. Output ONLY the corrected text, with no preamble."
    ),
    "reformulate": (
        "You rewrite dictated text to be clear, fluent and well-structured, "
        "keeping the original language and meaning. Output ONLY the rewritten "
        "text, with no preamble."
    ),
    "translate": (
        "You are a translator. Translate the user's text into {lang}. Preserve "
        "meaning, tone and formatting. Output ONLY the translation, with no "
        "preamble."
    ),
}

# Minimal ISO-639-1 → English name map for nicer translate prompts; unknown
# codes are passed through as-is (a full language name also works).
_LANG_NAMES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
    "hi": "Hindi", "pl": "Polish",
}


class PostProcessor:
    """LLM post-processing of dictation text (correct / reformulate / translate)."""

    @staticmethod
    def _lang_name(code: str) -> str:
        code = (code or "").strip()
        return _LANG_NAMES.get(code.lower(), code or "English")

    @staticmethod
    @safe_execute("PostProcess")
    def _run(text: str, system_prompt: str) -> Optional[str]:
        response = get_client().chat.completions.create(
            model=config_module.CFG.MODEL_CHAT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )
        out = (response.choices[0].message.content or "").strip()
        return out or None

    @classmethod
    def process(cls, text: str) -> str:
        """Return post-processed text, or the original on none/failure."""
        mode = (config_module.CFG.POSTPROCESS_MODE or "none").strip().lower()
        if not text or mode == "none" or mode not in _PROMPTS:
            return text

        system_prompt = _PROMPTS[mode]
        if mode == "translate":
            system_prompt = system_prompt.format(
                lang=cls._lang_name(config_module.CFG.POSTPROCESS_TARGET_LANG)
            )

        print(f"✨ Post-processing ({mode})…")
        result = cls._run(text, system_prompt)
        return result or text
