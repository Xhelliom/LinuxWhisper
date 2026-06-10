"""
API key storage — settable from the UI, persisted across reboots.

Keys live in ``~/.config/linuxwhisper/secrets.env`` (``KEY=value`` lines,
chmod 600). The app loads this file into ``os.environ`` at startup
(``load_secrets()`` in ``app.main``), so under the systemd --user
service-at-startup setup the keys are available on every boot/login — no
``environment.d`` entry required.

Precedence: only keys PRESENT in the file are injected, so a key already
provided by the real environment (e.g. ``environment.d``) keeps working when the
file doesn't define it. A key set via the UI is written to the file and
therefore becomes authoritative (overrides the inherited env on next startup,
and is applied live immediately on save).

Security note: keys are stored in plaintext (chmod 600), the same posture as an
``environment.d`` drop-in. Don't commit this file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

SECRETS_FILE: Path = Path.home() / ".config" / "linuxwhisper" / "secrets.env"

# API keys the UI manages, with a human label per provider.
MANAGED_KEYS = {
    "GROQ_API_KEY": "Groq",
    "OPENAI_API_KEY": "OpenAI",
    "DEEPGRAM_API_KEY": "Deepgram",
}


def read_secrets() -> Dict[str, str]:
    """Parse the secrets file into {KEY: value} (empty dict if absent/unreadable)."""
    if not SECRETS_FILE.exists():
        return {}
    out: Dict[str, str] = {}
    try:
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                out[key] = value
    except Exception as e:
        print(f"⚠️ Failed to read {SECRETS_FILE}: {e}")
    return out


def load_secrets() -> None:
    """Inject stored keys into os.environ (file-present keys win). Run at startup."""
    for key, value in read_secrets().items():
        if value:
            os.environ[key] = value


def save_secrets(values: Dict[str, str]) -> None:
    """
    Persist API keys and apply them to the live process.

    ``values`` maps key name → value; an empty/blank value clears that key (from
    the file and from os.environ). Only ``MANAGED_KEYS`` are written. The file is
    created chmod 600.
    """
    # Merge over whatever is already stored so unmanaged lines aren't lost.
    stored = read_secrets()
    for key, value in values.items():
        if key not in MANAGED_KEYS:
            continue
        value = (value or "").strip()
        if value:
            stored[key] = value
            os.environ[key] = value          # apply live
        else:
            stored.pop(key, None)
            os.environ.pop(key, None)         # clear live

    lines = ["# LinuxWhisper API keys — managed by the settings UI. chmod 600.\n"]
    lines += [f"{k}={v}\n" for k, v in stored.items() if v]

    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Write with restrictive perms from the start (avoid a brief world-readable window).
    fd = os.open(SECRETS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(lines)
    finally:
        try:
            os.chmod(SECRETS_FILE, 0o600)
        except OSError:
            pass
