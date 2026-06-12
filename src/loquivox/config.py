"""
Application configuration.

Defaults are defined in the ``Config`` dataclass below. They can be
overridden at runtime by an optional user file:

    ~/.config/loquivox/config.toml

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

CONFIG_FILE: Path = Path.home() / ".config" / "loquivox" / "config.toml"

# Refinement levels for post-processing — single source of truth for the
# settings slider and the tray submenu. 0 = off; higher = more rewriting. The
# per-level system prompts live in services.postprocess (_LEVEL_PROMPTS).
POSTPROCESS_LEVELS: tuple = (
    (0, "Off"),
    (1, "Correct"),
    (2, "Light"),
    (3, "Medium"),
    (4, "Strong"),
    (5, "Custom"),   # uses POSTPROCESS_CUSTOM_PROMPT
)
POSTPROCESS_MAX_LEVEL: int = 5
POSTPROCESS_CUSTOM_LEVEL: int = 5


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
    SETTINGS_FILE: Path = Path.home() / ".config" / "loquivox" / "settings.json"

    # --- Audio Settings ---
    SAMPLE_RATE: int = 44100

    # --- History Limits ---
    MAX_TOKENS: int = 32000
    ANSWER_HISTORY_LIMIT: int = 15
    CHAT_MESSAGE_LIMIT: int = 20
    CHAT_AUTO_HIDE_SEC: int = 3

    # --- AI Models ---
    MODEL_CHAT: str = "llama-3.3-70b-versatile"
    MODEL_VISION: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    MODEL_WHISPER: str = "whisper-large-v3"
    MODEL_TTS: str = "canopylabs/orpheus-v1-english"

    # --- Transcription backend selection ---
    # Which engine transcribes speech. See loquivox.transcription.
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

    # --- Post-processing (dictation text → LLM, opt-in) ---
    # Refinement intensity: 0 = off, 1 = correct … 4 = strong (POSTPROCESS_LEVELS).
    POSTPROCESS_LEVEL: int = 0
    # Translate instead of refine — a separate axis; uses POSTPROCESS_TARGET_LANG.
    POSTPROCESS_TRANSLATE: bool = False
    # Target language for translate (ISO-639-1 code or a language name).
    POSTPROCESS_TARGET_LANG: str = "en"
    # System prompt used by the "Custom" level (POSTPROCESS_CUSTOM_LEVEL).
    POSTPROCESS_CUSTOM_PROMPT: str = ""

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
    TEMP_TTS_PATH: str = f"/tmp/loquivox_tts_{os.getuid()}.wav"

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
    # format: "id": (Label, [chord specs])
    # A chord spec is "+"-joined: the LAST token is the trigger key; any leading
    # tokens are modifiers (ALT/CTRL/SHIFT/SUPER, or a specific key name). So a
    # spec is either a single key ("RIGHTALT", "F3") or a combo ("ALT+SPACE",
    # "CTRL+SHIFT+D"). evdev key names — work on both X11 and Wayland.
    # Override per-mode in config.toml under [hotkeys], e.g.
    #   dictation = ["ALT+SPACE", "F3"]
    HOTKEY_DEFS: Dict[str, Tuple[str, List[str]]] = field(default_factory=lambda: {
        "dictation":  ("R-Alt / F3", ["RIGHTALT", "F3", "F13"]),
        "ai":         ("F4",  ["F4", "F14"]),
        "ai_rewrite": ("F7",  ["F7", "PREVIOUSSONG"]),
        "vision":     ("F8",  ["F8", "PLAYPAUSE"]),
        "pin":        ("F9",  ["F9", "NEXTSONG"]),
        "tts":        ("F10", ["F10", "MUTE"]),
        # Cancel the active recording / in-flight transcription (no text inserted).
        "cancel":     ("Esc", ["ESC"]),
        # Pause / resume the current recording (capture is held, not stopped).
        "pause":      ("Space", ["SPACE"]),
        # Stop the recording and choose this dictation's refinement level.
        # Unbound by default — assign a key in Settings → Hotkeys.
        "refine":     ("(unset)", []),
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


# Modifier name → keycodes that satisfy it (any one held is enough).
_MODIFIER_ALIASES: Dict[str, Tuple[int, ...]] = {
    "ALT":     (ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT),
    "CTRL":    (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL),
    "CONTROL": (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL),
    "SHIFT":   (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
    "SUPER":   (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
    "META":    (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
    "WIN":     (ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA),
}

# Reverse map: a modifier keycode → its friendly name (used by chord capture).
_MODIFIER_NAME_BY_CODE: Dict[int, str] = {
    code: name
    for name in ("ALT", "CTRL", "SHIFT", "SUPER")
    for code in _MODIFIER_ALIASES[name]
}
#: keycodes recognised as modifiers (used by the capture UI to build combos).
MODIFIER_CODES = frozenset(_MODIFIER_NAME_BY_CODE)


def modifier_name(code: int):
    """Friendly modifier name for a keycode (KEY_LEFTALT → 'ALT'), or None."""
    return _MODIFIER_NAME_BY_CODE.get(code)


def _resolve_modifier(token: str) -> frozenset:
    """Resolve a modifier token to the set of keycodes that satisfy it."""
    key = token.strip().upper().replace(" ", "_")
    if key.startswith("KEY_"):
        key = key[4:]
    if key in _MODIFIER_ALIASES:
        return frozenset(_MODIFIER_ALIASES[key])
    return frozenset({_resolve_keycode(token)})  # a specific key acting as modifier


def parse_chord(spec: str) -> Tuple[int, Tuple[frozenset, ...]]:
    """
    Parse a chord spec into ``(trigger_keycode, (modifier_groups...))``.

    The last ``+``-separated token is the trigger; leading tokens are modifiers.
    ``"F3"`` → ``(KEY_F3, ())``; ``"ALT+SPACE"`` →
    ``(KEY_SPACE, ({KEY_LEFTALT, KEY_RIGHTALT},))``. Raises ``ValueError`` on an
    unknown key/modifier name.
    """
    parts = [p for p in spec.replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError(f"empty hotkey spec '{spec}'")
    *mods, trigger = parts
    return _resolve_keycode(trigger), tuple(_resolve_modifier(m) for m in mods)


def resolve_hotkeys(cfg: "Config") -> Dict[str, list]:
    """
    Parse every mode's chord specs into ``(trigger, modifier_groups)`` bindings
    for the keyboard listener. Unparseable specs are skipped with a warning.
    """
    resolved: Dict[str, list] = {}
    for mode, (_label, specs) in cfg.HOTKEY_DEFS.items():
        bindings = []
        for spec in specs:
            try:
                bindings.append(parse_chord(spec))
            except ValueError as e:
                print(f"⚠️ hotkey [{mode}]: {e} — skipped")
        resolved[mode] = bindings
    return resolved


def _build_hotkeys(
    base: Dict[str, Tuple[str, List[str]]],
    overrides: Dict[str, Any],
) -> Dict[str, Tuple[str, List[str]]]:
    """Apply [hotkeys] overrides (lists of chord specs) onto the base defs."""
    result = dict(base)
    for mode, specs in overrides.items():
        if mode not in result:
            print(f"⚠️ config.toml [hotkeys]: unknown mode '{mode}' — ignored")
            continue
        if isinstance(specs, str):
            specs = [specs]
        specs = [str(s).strip() for s in specs if str(s).strip()]
        if not specs:
            continue
        try:  # validate now so we never store a broken binding
            for s in specs:
                parse_chord(s)
        except ValueError as e:
            print(f"⚠️ config.toml [hotkeys.{mode}]: {e} — keeping default")
            continue
        result[mode] = (" / ".join(specs), specs)
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

    post = data.get("postprocess", {})
    # Back-compat: map the old `mode` (none/correct/reformulate/translate) onto
    # the new level + translate axes when the new keys aren't present.
    _mode_to_level = {"none": 0, "correct": 1, "reformulate": 2}
    if "level" in post:
        overrides["POSTPROCESS_LEVEL"] = max(0, min(POSTPROCESS_MAX_LEVEL, int(post["level"])))
    elif "mode" in post:
        m = str(post["mode"]).strip().lower()
        if m == "translate":
            overrides["POSTPROCESS_TRANSLATE"] = True
        elif m == "reformulate" and str(post.get("reformulate_prompt", "")).strip():
            # old reformulate + a custom prompt → the new Custom level
            overrides["POSTPROCESS_LEVEL"] = POSTPROCESS_CUSTOM_LEVEL
        else:
            overrides["POSTPROCESS_LEVEL"] = _mode_to_level.get(m, 0)
    if "translate" in post:
        overrides["POSTPROCESS_TRANSLATE"] = bool(post["translate"])
    if "target_language" in post:
        overrides["POSTPROCESS_TARGET_LANG"] = str(post["target_language"])
    # `reformulate_prompt` is the old name for the custom override.
    custom = post.get("custom_prompt", post.get("reformulate_prompt"))
    if custom is not None:
        overrides["POSTPROCESS_CUSTOM_PROMPT"] = str(custom).strip()

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


def reload_config() -> Config:
    """
    Re-read config.toml and rebuild the global ``CFG`` (used by the settings UI
    to apply edits live).

    Note: modules that did ``from loquivox.config import CFG`` keep their old
    reference, so settings that are only read at import/startup (e.g. overlay
    size) still need a service restart. Transcription settings apply live because
    the dispatcher is reconfigured from the returned fresh config; hotkeys apply
    live because KeyboardHandler.reload_hotkeys() rebuilds its keycode map from it.
    """
    global CFG
    CFG = _build_config()
    return CFG
