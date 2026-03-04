"""PCM/WAV decoding helpers.

Xiyu's API layer standardizes uploads to 16kHz, 16-bit, mono PCM (s16le) bytes.
For long-audio chunking we often need a float32 waveform in [-1, 1].

This module provides small utilities without requiring FFmpeg/librosa/soundfile.
"""

from __future__ import annotations

import io
import wave
from typing import Tuple

import numpy as np

__all__ = [
    "is_wav_bytes",
    "pcm16le_bytes_to_float32",
    "float32_to_pcm16le_bytes",
    "wav_bytes_to_float32",
]


def is_wav_bytes(data: bytes) -> bool:
    """Best-effort check for a RIFF/WAVE header."""
    return len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE"


def pcm16le_bytes_to_float32(pcm: bytes) -> np.ndarray:
    """Decode raw PCM16LE mono bytes into float32 waveform in [-1, 1]."""
    if not pcm:
        return np.zeros((0,), dtype=np.float32)

    # Ensure whole samples.
    if len(pcm) % 2 != 0:
        pcm = pcm[: len(pcm) - 1]

    audio_i16 = np.frombuffer(pcm, dtype=np.int16)
    return audio_i16.astype(np.float32) / 32768.0


def float32_to_pcm16le_bytes(audio: np.ndarray) -> bytes:
    """Encode float32 waveform in [-1, 1] to PCM16LE mono bytes."""
    if audio is None:
        return b""

    a = audio.astype(np.float32, copy=False)
    if a.size == 0:
        return b""

    a = np.clip(a, -1.0, 0.9999695)  # avoid overflow at +1.0
    audio_i16 = (a * 32768.0).astype(np.int16)
    return audio_i16.tobytes()


def wav_bytes_to_float32(wav_data: bytes) -> Tuple[np.ndarray, int]:
    """Decode WAV container bytes into (audio_float32, sample_rate).

    Only supports PCM16 mono WAV, which matches Xiyu's internal standard.
    """
    with wave.open(io.BytesIO(wav_data), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if channels != 1:
        raise ValueError(f"Only mono WAV is supported (channels=1), got {channels}")
    if sampwidth != 2:
        raise ValueError(f"Only 16-bit WAV is supported (sampwidth=2), got {sampwidth}")

    return pcm16le_bytes_to_float32(frames), int(sample_rate)
