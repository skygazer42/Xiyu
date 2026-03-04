"""PCM slicing helpers for diarization-based post-processing.

Xiyu's HTTP upload pipeline typically normalizes audio to 16kHz, mono, PCM16LE
bytes before passing it into the engine. Some paths (e.g. video extraction)
may provide WAV container bytes; for speaker-fallback we need raw PCM bytes for
ms-based slicing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from src.core.audio.pcm import (
    float32_to_pcm16le_bytes,
    is_wav_bytes,
    wav_bytes_to_float32,
)

__all__ = ["ensure_pcm16le_16k_mono_bytes", "slice_pcm16le"]


def ensure_pcm16le_16k_mono_bytes(audio_input: Union[bytes, str, Path]) -> bytes:
    """Best-effort normalize audio bytes into PCM16LE 16k mono bytes.

    - If input looks like WAV bytes, decode it and return PCM16LE bytes.
    - Otherwise, assume it already is PCM16LE 16k mono.
    """
    if isinstance(audio_input, (str, Path)):
        data = Path(audio_input).read_bytes()
    elif isinstance(audio_input, (bytes, bytearray)):
        data = bytes(audio_input)
    else:
        raise TypeError(f"Unsupported audio_input type: {type(audio_input)!r}")

    if not data:
        return b""

    if not is_wav_bytes(data):
        return data

    audio, sr = wav_bytes_to_float32(data)
    if sr != 16000:
        try:
            import librosa

            audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
        except Exception as e:
            raise ValueError(f"Unsupported WAV sample_rate={sr}, expected 16000") from e

    return float32_to_pcm16le_bytes(audio)


def slice_pcm16le(
    pcm16le: bytes,
    *,
    start_ms: int,
    end_ms: int,
    sample_rate: int = 16000,
) -> bytes:
    """Slice a PCM16LE mono buffer using millisecond boundaries."""
    if not pcm16le:
        return b""

    try:
        start_ms_int = int(start_ms)
    except (TypeError, ValueError):
        start_ms_int = 0
    try:
        end_ms_int = int(end_ms)
    except (TypeError, ValueError):
        end_ms_int = start_ms_int

    if start_ms_int < 0:
        start_ms_int = 0
    if end_ms_int < start_ms_int:
        end_ms_int = start_ms_int

    if sample_rate <= 0:
        sample_rate = 16000

    start_sample = int(start_ms_int * sample_rate / 1000)
    end_sample = int(end_ms_int * sample_rate / 1000)

    start_byte = start_sample * 2
    end_byte = end_sample * 2

    # Clamp and keep alignment to whole samples.
    n = len(pcm16le)
    if start_byte < 0:
        start_byte = 0
    if end_byte > n:
        end_byte = n
    start_byte -= start_byte % 2
    end_byte -= end_byte % 2

    if start_byte >= end_byte:
        return b""

    return pcm16le[start_byte:end_byte]
