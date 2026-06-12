"""
Optional post-processing of dictated text via the Groq LLM.

After transcription, the raw text can be cleaned up, reformulated, or translated
before it is typed. This is opt-in (default off) and runs off the GTK thread
(in the transcription worker), so it never freezes the UI. On any failure it
returns the original text unchanged.

Controlled by config ([postprocess] in config.toml):
  - ``level`` (0-4) — refinement intensity: 0 off, 1 correct, 2 light,
    3 medium, 4 strong. The per-level system prompts are defined below.
  - ``custom_prompt`` — overrides the level's prompt when non-empty.
  - ``translate`` (bool) + ``target_language`` — translate instead of refine.

Reads ``config.CFG`` through the module so settings changes apply live
(reload_config rebinds the module global).
"""
from __future__ import annotations

from typing import Optional

import loquivox.config as config_module
from loquivox.api import get_client
from loquivox.decorators import safe_execute

# System prompt per refinement level (1-4); level 0 = off (no call). Each insists
# on returning ONLY the resulting text so the output can be typed verbatim. They
# go from purely mechanical (1) to free rewriting (4).
_LEVEL_PROMPTS = {
    1: (  # Correct — errors only, no rephrasing
        "You fix dictated text: correct spelling, grammar, punctuation and "
        "capitalization only. Keep the original language, wording and meaning. "
        "Do not rephrase, add or remove content. Output ONLY the corrected "
        "text, with no preamble."
    ),
    2: (  # Light — minimal polish, intent-preserving
        "You lightly polish dictated text. Make the SMALLEST changes needed for "
        "clarity, fluency and punctuation. Preserve the original meaning, "
        "intent, tone, wording and language. Do NOT rephrase aggressively, and "
        "do NOT add, remove or reinterpret content. Output ONLY the text, with "
        "no preamble."
    ),
    3: (  # Medium — clearer/more fluent, may restructure
        "You rewrite dictated text to be clear and fluent. You may restructure "
        "sentences and adjust word choices for readability, but preserve the "
        "original meaning, intent and language, and add no new information. "
        "Output ONLY the text, with no preamble."
    ),
    4: (  # Strong — free rewrite for quality
        "You rewrite dictated text into clear, concise, well-structured prose. "
        "Rephrase freely to improve quality while keeping the core meaning, key "
        "facts and the original language. Output ONLY the text, with no preamble."
    ),
}

_TRANSLATE_PROMPT = (
    "You are a translator. Translate the user's text into {lang}. Preserve "
    "meaning, tone and formatting. Output ONLY the translation, with no preamble."
)

# Minimal ISO-639-1 → English name map for nicer translate prompts; unknown
# codes are passed through as-is (a full language name also works).
_LANG_NAMES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic",
    "hi": "Hindi", "pl": "Polish",
}


class PostProcessor:
    """LLM post-processing of dictation text (leveled refinement, or translate)."""

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
    def _prompt_for_level(cls, level: int) -> Optional[str]:
        """System prompt for a refinement level, or None for off / empty Custom."""
        if level == config_module.POSTPROCESS_CUSTOM_LEVEL:
            return (config_module.CFG.POSTPROCESS_CUSTOM_PROMPT or "").strip() or None
        return _LEVEL_PROMPTS.get(level)

    @classmethod
    def process(cls, text: str, level_override: Optional[int] = None) -> str:
        """
        Return post-processed text, or the original when off / on failure.

        ``level_override`` (0-5) forces a refinement level for this one call —
        used by the on-the-fly chooser — bypassing the configured level/translate.
        """
        cfg = config_module.CFG
        if not text:
            return text

        if level_override is None and cfg.POSTPROCESS_TRANSLATE:
            prompt = _TRANSLATE_PROMPT.format(lang=cls._lang_name(cfg.POSTPROCESS_TARGET_LANG))
            label = f"translate → {cfg.POSTPROCESS_TARGET_LANG}"
        else:
            level = int(cfg.POSTPROCESS_LEVEL if level_override is None else level_override)
            prompt = cls._prompt_for_level(level)
            if not prompt:
                return text  # off, or Custom with no prompt
            label = f"level {level}"

        print(f"✨ Post-processing ({label})…")
        result = cls._run(text, prompt)
        return result or text
