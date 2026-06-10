"""
Round-trip writer for ``config.toml`` (settings UI, #15).

Reading config is done by ``config.py`` with the stdlib ``tomllib``; writing
needs to PRESERVE the user's comments and formatting, which ``tomllib`` can't
do — so the writer uses ``tomlkit``. tomlkit is imported lazily so the rest of
the app never depends on it just to start.

The public helper ``update_section`` merges a flat ``{key: value}`` mapping into
a ``[section]`` table, creating the file/table as needed, and writes it back.
"""
from __future__ import annotations

from typing import Any, Dict

from linuxwhisper.config import CONFIG_FILE


class ConfigWriteError(RuntimeError):
    """Raised when config.toml could not be written."""


def _load_document():
    """Parse the existing config.toml (comments preserved), or a new document."""
    import tomlkit

    if CONFIG_FILE.exists():
        try:
            return tomlkit.parse(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            raise ConfigWriteError(
                f"{CONFIG_FILE} is not valid TOML ({e}); refusing to overwrite. "
                "Fix or remove it, then retry."
            ) from e
    return tomlkit.document()


def update_section(section: str, values: Dict[str, Any]) -> None:
    """
    Merge ``values`` into ``[section]`` of config.toml and write it back,
    preserving existing comments/formatting. Creates the file and table if
    absent. ``None`` values are skipped; empty strings ARE written (they
    intentionally override a default, e.g. ``language = ""``).
    """
    import tomlkit

    doc = _load_document()
    if section not in doc:
        doc[section] = tomlkit.table()
    table = doc[section]
    for key, value in values.items():
        if value is None:
            continue
        table[key] = value

    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(tomlkit.dumps(doc), encoding="utf-8")
    except OSError as e:
        raise ConfigWriteError(f"Could not write {CONFIG_FILE}: {e}") from e
