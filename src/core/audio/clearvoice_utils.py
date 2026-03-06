"""ClearVoice (ClearerVoice-Studio) helpers shared across service/client.

We support both the official model names used by ClearVoice and a few common
aliases used in ops configs.
"""

from __future__ import annotations

import logging
from math import gcd
from typing import Dict

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "normalize_clearvoice_model_name",
    "clearvoice_model_sample_rate",
    "resample_audio",
]


# Some deployments/configs use informal names like "MossFormer2_48000Hz".
# Normalize them to ClearVoice official IDs so checkpoint download works.
_MODEL_ALIASES: Dict[str, str] = {
    # User-friendly / legacy aliases -> official ClearVoice model IDs
    "mossformer2_48000hz": "MossFormer2_SE_48K",
    "mossformer2_48k": "MossFormer2_SE_48K",
    "mossformer2_se_48k": "MossFormer2_SE_48K",
    # Keep existing supported 16k models intact
    "frcrn_se_16k": "FRCRN_SE_16K",
    "mossformergan_se_16k": "MossFormerGAN_SE_16K",
}


def normalize_clearvoice_model_name(model_name: str) -> str:
    """Normalize ClearVoice model name to an official ID.

    Returns a non-empty string that matches upstream ClearVoice model IDs such
    as `FRCRN_SE_16K` / `MossFormerGAN_SE_16K` / `MossFormer2_SE_48K`.
    """
    s = str(model_name or "").strip()
    if not s:
        return "FRCRN_SE_16K"

    key = s.strip().lower()
    if key in _MODEL_ALIASES:
        return _MODEL_ALIASES[key]

    # Users might already pass the official ID (case-sensitive). Keep as-is.
    return s


def clearvoice_model_sample_rate(model_name: str) -> int:
    """Best-effort mapping from model name -> required sampling rate."""
    name = normalize_clearvoice_model_name(model_name)
    if name == "MossFormer2_SE_48K":
        return 48000
    # Default to 16k: FRCRN_SE_16K / MossFormerGAN_SE_16K etc.
    return 16000


def resample_audio(audio: np.ndarray, *, sr_from: int, sr_to: int) -> np.ndarray:
    """Resample 1D float32 audio.

    This is best-effort and prefers SciPy's `resample_poly` when available. Falls
    back to librosa if SciPy isn't available in the runtime.
    """
    try:
        sr_from_i = int(sr_from)
        sr_to_i = int(sr_to)
    except Exception:
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if x.size == 0 or sr_from_i <= 0 or sr_to_i <= 0 or sr_from_i == sr_to_i:
        return x

    try:
        g = gcd(sr_from_i, sr_to_i)
        up = sr_to_i // g
        down = sr_from_i // g
        if up <= 0 or down <= 0:
            return x

        from scipy.signal import resample_poly  # type: ignore

        y = resample_poly(x, up=up, down=down)
        return np.asarray(y, dtype=np.float32).reshape(-1)
    except Exception as e:
        # librosa is slower but widely available in this repo's runtime deps.
        try:
            import librosa  # type: ignore

            y = librosa.resample(x, orig_sr=sr_from_i, target_sr=sr_to_i)
            return np.asarray(y, dtype=np.float32).reshape(-1)
        except Exception:
            logger.warning("resample_audio failed (sr_from=%s sr_to=%s): %s", sr_from_i, sr_to_i, e)
            return x

