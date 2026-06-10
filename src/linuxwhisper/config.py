"""
Application configuration.

Defaults are defined in the ``Config`` dataclass below. They can be
overridden at runtime by an optional user file:

    ~/.config/linuxwhisper/config.toml

The TOML is loaded once at import time and layered on top of the defaults
(see ``_build_config``). A missing or malformed file falls back to defaults,
so the app always starts.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

from evdev import ecodes

CONFIG_FILE: Path = Path.home() / ".config" / "linuxwhisper" / "config.toml"


@dataclass(frozen=True)
class Config:
    """
    Immutable application configuration.

    Defaults live here; user overrides come from config.toml (see module
    docstring). To change a setting permanently without editing source,
    edit config.toml.
    """
    # --- Global Design System (Curated Color Schemes) ---
    COLOR_SCHEMES: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "Arctic Twilight": {
            "bg":        "#003049",
            "surface":   "#335c67",
            "accent":    "#669bbc",
            "text":      "#f1faee",
            "desc":      "Infinite polar blues echoing the cosmos and the soft light of evening snow."
        },
        "Volcanic Dawn": {
            "bg":        "#003049",
            "surface":   "#669bbc",
            "accent":    "#780000",
            "text":      "#fdf0d5",
            "desc":      "Intense, blazing crimson radiates energy; a bold and dramatic command of attention."
        },
        "Spring Blossom": {
            "bg":        "#a2d2ff",
            "surface":   "#cdb4db",
            "accent":    "#ffafcc",
            "text":      "#322659",
            "desc":      "Delicate pastel petals and whimsical sky blues, bringing a soft, elegant charm."
        },
        "Deep Sea Myth": {
            "bg":        "#0B0C10",
            "surface":   "#1F2833",
            "accent":    "#66FCF1",
            "text":      "#C5C6C7",
            "desc":      "Mysterious abyssal depths where cyan phosphorescence meets resilient silent strength."
        },
        "Boreal Silence": {
            "bg":        "#041C06",
            "surface":   "#064E3B",
            "accent":    "#10B981",
            "text":      "#ECFDF5",
            "desc":      "Lush, dark greens of ancient forests whispering beneath the mint-bright aurora."
        },
        "Oceanic Zen": {
            "bg":        "#002b36",
            "surface":   "#073642",
            "accent":    "#2aa198",
            "text":      "#eee8d5",
            "desc":      "Mathematically balanced depths of solarized teal, a classic of modern interface harmony."
        },
        "Amber Harvest": {
            "bg":        "#282828",
            "surface":   "#3c3836",
            "accent":    "#d65d0e",
            "text":      "#fbf1c7",
            "desc":      "Warm engineered earth tones of copper and cream, evoking a retro-industrial rustic elegance."
        },
        "Neon Nightshade": {
            "bg":        "#282a36",
            "surface":   "#44475a",
            "accent":    "#bd93f9",
            "text":      "#f8f8f2",
            "desc":      "Vibrant high-contrast purple and deep ink-blue, capturing the electric glow of a bioluminescent forest."
        },
        "Mediterranean Shore": {
            "bg":        "#264653",
            "surface":   "#2a9d8f",
            "accent":    "#f4a261",
            "text":      "#fdf1d3",
            "desc":      "Sun-drenched golden sands meet the smoky blue of midnight tides and crystalline waters."
        },
    })
    DEFAULT_SCHEME: str = "Oceanic Zen"
    SETTINGS_FILE: Path = Path.home() / ".config" / "linuxwhisper" / "settings.json"

    # --- Audio Settings ---
    SAMPLE_RATE: int = 44100

    # --- History Limits ---
    MAX_TOKENS: int = 32000
    ANSWER_HISTORY_LIMIT: int = 15
    CHAT_MESSAGE_LIMIT: int = 20
    CHAT_AUTO_HIDE_SEC: int = 3

    # --- AI Models ---
    MODEL_CHAT: str = "moonshotai/kimi-k2-instruct"
    MODEL_VISION: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    MODEL_WHISPER: str = "whisper-large-v3"
    MODEL_TTS: str = "canopylabs/orpheus-v1-english"

    # --- Transcription backend selection ---
    # Which engine transcribes speech. See linuxwhisper.transcription.
    #   "groq"           — Groq Cloud Whisper, batch (default, needs GROQ_API_KEY)
    #   "whispercpp"     — local whisper.cpp, offline, no key (needs [local] extra)
    #   "openai_realtime"/"deepgram" — streaming live (added in the streaming phase)
    #   "auto"           — Groq if a key is present, else whispercpp
    BACKEND: str = "groq"
    # Offline backend used automatically when the primary is unavailable
    # (no network / no key / API error). "" disables fallback.
    FALLBACK_BACKEND: str = "whispercpp"
    # Local whisper.cpp model name (auto-downloads): tiny, base, small, medium,
    # large-v3, large-v3-turbo, and .en variants.
    WHISPERCPP_MODEL: str = "base"
    # Streaming-backend models (used in the streaming phase).
    OPENAI_MODEL: str = "gpt-4o-transcribe"
    DEEPGRAM_MODEL: str = "nova-3"

    # --- Transcription ---
    # ISO-639-1 code (e.g. "en", "fr"). Empty string = Whisper autodetects.
    WHISPER_LANGUAGE: str = ""
    # Skip transcription (and the API call) for clips shorter than this, in
    # seconds — filters accidental key-clicks. 0 disables the check.
    MIN_AUDIO_SEC: float = 0.5
    # Audio is resampled to this rate before upload (Whisper runs at 16 kHz, so
    # smaller uploads = lower latency, no quality loss). 0 disables resampling.
    UPLOAD_SAMPLE_RATE: int = 16000

    # Phrases Whisper commonly hallucinates on silence — dropped before
    # insertion. Overridable via [transcription] hallucinations = [...].
    HALLUCINATIONS: frozenset = frozenset({
        # English
        "thank you", "you're welcome", "thanks", "subtitle", "you", "bye",
        # German (Whisper artifacts)
        "untertitel",
        # French
        "merci", "merci beaucoup", "sous-titres", "sous-titrage",
        "abonnez-vous", "merci d'avoir regardé", "au revoir",
    })

    # --- Clipboard timing (seconds) ---
    CLIPBOARD_PASTE_DELAY: float = 0.05
    CLIPBOARD_RESTORE_DELAY: float = 0.03
    # Cache window for focused-window / terminal detection (seconds).
    TERMINAL_CACHE_TTL: float = 0.2

    # --- TTS Voices ---
    TTS_VOICES: Tuple[str, ...] = ("diana", "hannah", "autumn", "austin", "daniel", "troy")
    TTS_DEFAULT_VOICE: str = "diana"
    TTS_MAX_CHARS: int = 4000

    # --- Recording Overlay Geometry ---
    OVERLAY_WIDTH: int = 220
    OVERLAY_HEIGHT: int = 60

    # --- Temp File Paths ---
    TEMP_SCREEN_PATH: str = f"/tmp/temp_screen_{os.getuid()}.png"
    TEMP_TTS_PATH: str = f"/tmp/linuxwhisper_tts_{os.getuid()}.wav"

    # --- System Prompt ---
    SYSTEM_PROMPT: str = (
        "Act as a compassionate assistant. Base your reasoning on the principles of "
        "Nonviolent Communication and A Course in Miracles. Apply these frameworks as "
        "your underlying logic without explicitly naming them or forcing them. Let your "
        "output be grounded, clear, and highly concise. Return ONLY the direct response."
    )

    # --- Mode Definitions (icon, overlay text, colors) ---
    MODES: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "dictation":  {"icon": "🎙️", "text": "Listening...",    "bg": "bg", "fg": "accent"},
        "ai":         {"icon": "🤖", "text": "AI Listening...", "bg": "bg", "fg": "accent"},
        "ai_rewrite": {"icon": "✍️", "text": "Rewrite Mode...", "bg": "bg", "fg": "accent"},
        "vision":     {"icon": "📸", "text": "Vision Mode...",  "bg": "bg", "fg": "accent"},
    })

    # --- Hotkey Definitions ---
    # format: "id": (Label, PrimaryKeycode, [ExtraKeycodes])
    # Uses evdev ecodes — works on both X11 and Wayland.
    # Override per-mode in config.toml under [hotkeys] using key names, e.g.
    #   dictation = ["RIGHTALT", "F3", "F13"]
    HOTKEY_DEFS: Dict[str, Tuple[str, int, List[int]]] = field(default_factory=lambda: {
        "dictation":  ("R-Alt / F3",  ecodes.KEY_RIGHTALT,  [ecodes.KEY_F3, ecodes.KEY_F13]),
        "ai":         ("F4",  ecodes.KEY_F4,  [ecodes.KEY_F14]),
        "ai_rewrite": ("F7",  ecodes.KEY_F7,  [ecodes.KEY_PREVIOUSSONG]),
        "vision":     ("F8",  ecodes.KEY_F8,  [ecodes.KEY_PLAYPAUSE]),
        "pin":        ("F9",  ecodes.KEY_F9,  [ecodes.KEY_NEXTSONG]),
        "tts":        ("F10", ecodes.KEY_F10, [ecodes.KEY_MUTE]),
    })


# ---------------------------------------------------------------------------
# User config loading (config.toml -> overrides on top of defaults)
# ---------------------------------------------------------------------------
def _load_user_toml() -> Dict[str, Any]:
    """Read config.toml, returning {} if absent or malformed."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"⚠️ Failed to parse {CONFIG_FILE}: {e} — using defaults")
        return {}


def _resolve_keycode(name: str) -> int:
    """
    Resolve an evdev key name to its keycode.

    Accepts "F3", "KEY_F3", "RIGHTALT", "right alt" (case/underscore tolerant).
    Raises ValueError on an unknown name.
    """
    candidate = name.strip().upper().replace(" ", "_")
    if not candidate.startswith("KEY_"):
        candidate = f"KEY_{candidate}"
    code = getattr(ecodes, candidate, None)
    if not isinstance(code, int):
        raise ValueError(f"unknown key name '{name}'")
    return code


def _build_hotkeys(
    base: Dict[str, Tuple[str, int, List[int]]],
    overrides: Dict[str, Any],
) -> Dict[str, Tuple[str, int, List[int]]]:
    """Apply [hotkeys] overrides (lists of key names) onto the base defs."""
    result = dict(base)
    for mode, names in overrides.items():
        if mode not in result:
            print(f"⚠️ config.toml [hotkeys]: unknown mode '{mode}' — ignored")
            continue
        if isinstance(names, str):
            names = [names]
        try:
            codes = [_resolve_keycode(n) for n in names]
        except ValueError as e:
            print(f"⚠️ config.toml [hotkeys.{mode}]: {e} — keeping default")
            continue
        if not codes:
            continue
        label = " / ".join(str(n).upper().replace("KEY_", "") for n in names)
        result[mode] = (label, codes[0], codes[1:])
    return result


def _build_config() -> Config:
    """Construct the global Config, layering config.toml over the defaults."""
    data = _load_user_toml()
    if not data:
        return Config()

    base = Config()
    overrides: Dict[str, Any] = {}

    trans = data.get("transcription", {})
    if "backend" in trans:
        overrides["BACKEND"] = str(trans["backend"])
    if "fallback" in trans:
        overrides["FALLBACK_BACKEND"] = str(trans["fallback"])
    if "whispercpp_model" in trans:
        overrides["WHISPERCPP_MODEL"] = str(trans["whispercpp_model"])
    if "openai_model" in trans:
        overrides["OPENAI_MODEL"] = str(trans["openai_model"])
    if "deepgram_model" in trans:
        overrides["DEEPGRAM_MODEL"] = str(trans["deepgram_model"])
    if "model" in trans:
        overrides["MODEL_WHISPER"] = str(trans["model"])
    if "language" in trans:
        overrides["WHISPER_LANGUAGE"] = str(trans["language"])
    if "sample_rate" in trans:
        overrides["SAMPLE_RATE"] = int(trans["sample_rate"])
    if "min_audio_sec" in trans:
        overrides["MIN_AUDIO_SEC"] = float(trans["min_audio_sec"])
    if "upload_sample_rate" in trans:
        overrides["UPLOAD_SAMPLE_RATE"] = int(trans["upload_sample_rate"])
    if "hallucinations" in trans:
        overrides["HALLUCINATIONS"] = frozenset(
            str(h).strip().lower() for h in trans["hallucinations"]
        )

    clip = data.get("clipboard", {})
    if "paste_delay" in clip:
        overrides["CLIPBOARD_PASTE_DELAY"] = float(clip["paste_delay"])
    if "restore_delay" in clip:
        overrides["CLIPBOARD_RESTORE_DELAY"] = float(clip["restore_delay"])
    if "terminal_cache_ttl" in clip:
        overrides["TERMINAL_CACHE_TTL"] = float(clip["terminal_cache_ttl"])

    models = data.get("models", {})
    if "chat" in models:
        overrides["MODEL_CHAT"] = str(models["chat"])
    if "vision" in models:
        overrides["MODEL_VISION"] = str(models["vision"])
    if "tts" in models:
        overrides["MODEL_TTS"] = str(models["tts"])

    overlay = data.get("overlay", {})
    if "width" in overlay:
        overrides["OVERLAY_WIDTH"] = int(overlay["width"])
    if "height" in overlay:
        overrides["OVERLAY_HEIGHT"] = int(overlay["height"])

    hotkeys = data.get("hotkeys", {})
    if hotkeys:
        overrides["HOTKEY_DEFS"] = _build_hotkeys(base.HOTKEY_DEFS, hotkeys)

    return replace(base, **overrides)


# Global config instance
CFG = _build_config()
