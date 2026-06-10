"""
Shared audio-prep helpers for transcription backends.

Recording is captured as float32 mono at ``CFG.SAMPLE_RATE`` (shape ``(N, 1)``).
Backends need it in different shapes — Groq wants a 16 kHz WAV upload, local
whisper.cpp wants a 1-D float32 array at exactly 16 kHz — so the conversions
live here rather than being duplicated per backend.
"""
from __future__ import annotations

import io
from math import gcd
from typing import Tuple

import numpy as np
from scipy.io.wavfile import write as wav_write
from scipy.signal import resample_poly

WHISPER_RATE = 16000  # every Whisper variant runs internally at 16 kHz


def resample_down(audio: np.ndarray, src_rate: int, target_rate: int) -> Tuple[np.ndarray, int]:
    """
    Downsample ``audio`` to ``target_rate``. No-op if the target is 0/disabled
    or not below the source rate (we never upsample on this path — it would only
    inflate the payload with no quality gain).
    """
    if not target_rate or target_rate >= src_rate:
        return audio, src_rate
    g = gcd(int(target_rate), int(src_rate))
    resampled = resample_poly(audio, target_rate // g, src_rate // g, axis=0)
    return resampled.astype(np.float32), target_rate


def to_mono_16k(audio: np.ndarray, src_rate: int) -> np.ndarray:
    """
    Return a contiguous 1-D float32 array at exactly 16 kHz, mono — the format
    pywhispercpp expects for a raw numpy input. Resamples up or down as needed.
    """
    data = audio
    if src_rate != WHISPER_RATE:
        g = gcd(WHISPER_RATE, int(src_rate))
        data = resample_poly(data, WHISPER_RATE // g, src_rate // g, axis=0)
    if data.ndim > 1:
        data = data.reshape(data.shape[0], -1).mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def write_wav(audio: np.ndarray, rate: int) -> io.BytesIO:
    """Encode ``audio`` to an in-memory WAV buffer ready for upload."""
    buf = io.BytesIO()
    buf.name = "audio.wav"
    wav_write(buf, rate, audio)
    buf.seek(0)
    return buf
