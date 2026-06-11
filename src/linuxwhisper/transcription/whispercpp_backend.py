"""
Local whisper.cpp backend (via the standalone ``whisper-cli`` binary).

Runs fully offline, needs no API key, and therefore doubles as the automatic
offline FALLBACK when a cloud backend is unavailable (no network / no key /
API error).

Unlike the old binding-based backend, this talks to the **whisper.cpp engine
as a separate executable** (``whisper-cli``), invoked over ``subprocess`` —
exactly like the platform tools (xdotool/wtype/grim). That keeps the offline
engine out of the Python dependency matrix: it ships as a bundled binary in
every package format (.deb / AUR / AppImage) instead of needing the
``pywhispercpp`` wheel, which is packaged nowhere.

Binary discovery (first match wins):
  1. ``$LINUXWHISPER_WHISPER_CLI`` (explicit override)
  2. a binary bundled next to the install (``<prefix>/lib/linuxwhisper/``)
  3. ``whisper-cli`` (or legacy names) found on ``$PATH``

The ggml model is downloaded on first use (``prewarm()``) to
``~/.local/share/linuxwhisper/models/`` so the offline fallback is ready even
with no network at dictation time.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from scipy.io.wavfile import write as wav_write

from .base import BackendUnavailable, TranscriptionBackend
from .util import WHISPER_RATE, to_mono_16k

# whisper.cpp emits non-speech markers on silence/noise, e.g. "[BLANK_AUDIO]",
# "[ Silence ]", "(music)". Drop segments that are entirely such a marker so we
# never type them as dictated text.
_NON_SPEECH = re.compile(r"^\s*[\[(].*?[\])]\s*$")

# Candidate executable names, newest first ("main" was the pre-2024 name).
_BINARY_NAMES = ("whisper-cli", "whisper.cpp", "whisper-cpp", "main")

# HuggingFace mirror of the official ggml models.
_MODEL_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"


def _find_binary() -> Optional[str]:
    """Locate the whisper.cpp CLI executable, or None if unavailable."""
    override = os.environ.get("LINUXWHISPER_WHISPER_CLI")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override

    # Bundled next to the install (set up by the packaging recipes).
    bundled = Path(sys.prefix) / "lib" / "linuxwhisper" / "whisper-cli"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)

    for name in _BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


class WhisperCppBackend(TranscriptionBackend):
    """Offline transcription via the whisper.cpp ``whisper-cli`` binary."""

    name = "whispercpp"
    supports_streaming = False

    # Discovery is process-wide and won't change at runtime; cache it.
    _binary_cache: Optional[str] = None
    _binary_probed: bool = False
    _probe_lock = threading.Lock()

    def __init__(self, model: str, n_threads: int = 4) -> None:
        self._model_name = model
        self._n_threads = n_threads

    # -- engine / model discovery --------------------------------------------

    @classmethod
    def _binary(cls) -> Optional[str]:
        if not cls._binary_probed:
            with cls._probe_lock:
                if not cls._binary_probed:
                    cls._binary_cache = _find_binary()
                    cls._binary_probed = True
        return cls._binary_cache

    @staticmethod
    def _models_dir() -> Path:
        return Path.home() / ".local" / "share" / "linuxwhisper" / "models"

    def _model_path(self) -> Path:
        """
        Resolve the configured model to a ggml file path.

        Accepts either a bare model name ("base", "small.en", …) → resolved to
        ``<models_dir>/ggml-<name>.bin``, or a direct path to a .bin file.
        """
        m = self._model_name
        if os.sep in m or m.endswith(".bin"):
            return Path(m).expanduser()
        return self._models_dir() / f"ggml-{m}.bin"

    def is_available(self) -> bool:
        """Cheap readiness probe: the engine binary is present."""
        return self._binary() is not None

    def is_model_downloaded(self) -> bool:
        """True if the configured model's ggml file is already on disk."""
        return self._model_path().is_file()

    def local_status(self) -> Tuple[bool, bool]:
        """(engine_present, model_downloaded) — for the settings UI."""
        return self.is_available(), self.is_model_downloaded()

    def _download_model(self) -> None:
        """Fetch the ggml model to the local models dir (atomic via .part)."""
        m = self._model_name
        if os.sep in m or m.endswith(".bin"):
            # A direct path was configured but the file is missing — we can't
            # guess a download URL for an arbitrary path.
            raise BackendUnavailable(f"whisper.cpp model not found: {m}")

        dest = self._model_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{_MODEL_BASE_URL}/ggml-{m}.bin"
        tmp = dest.with_suffix(".bin.part")
        print(f"⬇️  Downloading whisper.cpp model '{m}' from {url}")
        try:
            with urllib.request.urlopen(url) as resp, open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh)
            tmp.replace(dest)
        except Exception as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise BackendUnavailable(
                f"whisper.cpp model download failed for '{m}': {e}"
            ) from e

    def prewarm(self) -> None:
        """Download the model on first run so the offline fallback is ready."""
        try:
            if self.is_available() and not self.is_model_downloaded():
                self._download_model()
        except Exception as e:  # never crash startup over a prewarm failure
            print(f"⚠️ whisper.cpp prewarm failed: {e}")

    # -- transcription -------------------------------------------------------

    def _build_cmd(self, binary: str, model: Path, wav: str, out_base: str,
                   language: str) -> List[str]:
        cmd = [
            binary,
            "-m", str(model),
            "-f", wav,
            "-oj", "-of", out_base,   # JSON output to <out_base>.json
            "-nt", "-np",             # no timestamps, no progress prints
            "-t", str(self._n_threads),
            "-l", language or "auto",
        ]
        return cmd

    @staticmethod
    def _parse_json(out_json: Path) -> str:
        data = json.loads(out_json.read_text(encoding="utf-8"))
        parts = []
        for seg in data.get("transcription", []):
            t = (seg.get("text") or "").strip()
            if t and not _NON_SPEECH.match(t):
                parts.append(t)
        return " ".join(parts).strip()

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> Optional[str]:
        binary = self._binary()
        if binary is None:
            raise BackendUnavailable(
                "whisper.cpp engine (whisper-cli) not found — bundled binary "
                "missing and none on PATH"
            )
        model = self._model_path()
        if not model.is_file():
            # Last-ditch: try to fetch it now (e.g. fallback fired before prewarm).
            self._download_model()

        data = to_mono_16k(audio, sample_rate)
        pcm16 = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)

        with tempfile.TemporaryDirectory(prefix="lw-whispercpp-") as tmp:
            wav_path = os.path.join(tmp, "audio.wav")
            out_base = os.path.join(tmp, "out")
            wav_write(wav_path, WHISPER_RATE, pcm16)

            cmd = self._build_cmd(binary, model, wav_path, out_base, language)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            except Exception as e:
                raise BackendUnavailable(f"whisper.cpp invocation failed: {e}") from e

            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                why = tail[-1] if tail else "see logs"
                raise BackendUnavailable(f"whisper.cpp failed (rc={proc.returncode}): {why}")

            out_json = Path(out_base + ".json")
            if not out_json.is_file():
                raise BackendUnavailable("whisper.cpp produced no JSON output")
            try:
                text = self._parse_json(out_json)
            except Exception as e:
                raise BackendUnavailable(f"whisper.cpp output parse error: {e}") from e

        return text or None
