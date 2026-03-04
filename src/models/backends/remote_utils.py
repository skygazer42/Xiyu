"""Utilities for remote ASR backends.

Xiyu's API layer converts uploads to 16kHz, 16-bit, mono PCM (s16le) bytes.
Remote ASR services typically expect a container format (e.g. WAV) via HTTP.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Tuple, Union


def pcm16le_to_wav_bytes(
    pcm: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sampwidth: int = 2,
) -> bytes:
    """Wrap raw PCM16LE bytes into a WAV container and return bytes."""
    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM (sampwidth=2) is supported")
    if channels != 1:
        raise ValueError("Only mono audio (channels=1) is supported")
    if len(pcm) % (sampwidth * channels) != 0:
        raise ValueError("PCM buffer size must align to whole samples")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def audio_input_to_wav_bytes(
    audio_input: Union[bytes, str, Path],
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    sampwidth: int = 2,
) -> Tuple[bytes, float]:
    """Convert supported audio input into (wav_bytes, duration_seconds).

    - bytes: treated as Xiyu internal PCM16LE 16k mono.
    - path/str: treated as an existing audio container; bytes are read and returned as-is.
      Duration is best-effort (WAV only).
    """
    if isinstance(audio_input, (str, Path)):
        p = Path(audio_input)
        data = p.read_bytes()
        duration_s = 0.0
        if p.suffix.lower() == ".wav":
            try:
                with wave.open(str(p), "rb") as wf:
                    frames = wf.getnframes()
                    sr = wf.getframerate() or 1
                    duration_s = float(frames) / float(sr)
            except Exception:
                duration_s = 0.0
        return data, duration_s

    if isinstance(audio_input, (bytes, bytearray)):
        pcm = bytes(audio_input)
        wav = pcm16le_to_wav_bytes(
            pcm,
            sample_rate=sample_rate,
            channels=channels,
            sampwidth=sampwidth,
        )
        duration_s = float(len(pcm)) / float(sampwidth * channels * sample_rate)
        return wav, duration_s

    raise TypeError(f"Unsupported audio_input type: {type(audio_input)!r}")
