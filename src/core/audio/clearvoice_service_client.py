"""ClearVoice microservice client (remote speech enhancement).

When `settings.clearvoice_service_base_url` is configured, Xiyu can offload
ClearVoice denoise to a dedicated container instead of importing the heavy
ClearerVoice-Studio stack in each ASR backend container.

The service API is intentionally simple:
  POST {base_url}/api/v1/enhance  (multipart: file=@audio.wav)
  -> returns WAV (16kHz, mono, PCM16) bytes.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
import numpy as np

from src.config import settings
from src.core.audio.pcm import float32_to_pcm16le_bytes, wav_bytes_to_float32
from src.models.backends.remote_utils import pcm16le_to_wav_bytes

logger = logging.getLogger(__name__)


def _response_body_head(resp: httpx.Response, *, limit: int = 4096) -> str:
    try:
        data = resp.content or b""
    except Exception:
        return ""
    if not data:
        return ""
    head = data[: max(0, int(limit))]
    try:
        return head.decode("utf-8", errors="replace")
    except Exception:
        return repr(head)


def clearvoice_service_enhance(
    audio: np.ndarray,
    *,
    sample_rate: int = 16000,
    base_url: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> np.ndarray:
    """Enhance audio via the ClearVoice service and return float32 waveform.

    Args:
        audio: 1D float32 waveform in [-1, 1].
        sample_rate: input/output sample rate (must be 16000 for ClearVoice).
        base_url: override service base URL (defaults to settings).
        timeout_s: override request timeout seconds (defaults to settings).
    """
    if sample_rate != 16000:
        raise ValueError(f"ClearVoice service expects 16kHz audio, got {sample_rate}Hz")

    if audio is None:
        return np.zeros((0,), dtype=np.float32)

    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return x

    base = (str(base_url) if base_url is not None else str(getattr(settings, "clearvoice_service_base_url", "")))
    base = str(base or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("ClearVoice service is not configured (CLEARVOICE_SERVICE_BASE_URL is empty)")

    try:
        timeout = float(timeout_s if timeout_s is not None else getattr(settings, "clearvoice_service_timeout_s", 600.0))
    except Exception:
        timeout = 600.0
    if timeout <= 0:
        timeout = 600.0

    pcm = float32_to_pcm16le_bytes(x)
    wav = pcm16le_to_wav_bytes(pcm, sample_rate=sample_rate, channels=1, sampwidth=2)

    files = {"file": ("audio.wav", wav, "audio/wav")}
    url = f"{base}/api/v1/enhance"

    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(url, files=files)
            resp.raise_for_status()
            out_wav = bytes(resp.content or b"")
    except httpx.HTTPStatusError as e:
        body = _response_body_head(e.response)
        raise RuntimeError(f"ClearVoice service HTTP {e.response.status_code}: {body}".strip()) from e
    except Exception as e:
        raise RuntimeError(f"ClearVoice service request failed: {e}") from e

    y, out_sr = wav_bytes_to_float32(out_wav)
    if int(out_sr) != int(sample_rate):
        raise RuntimeError(f"ClearVoice service returned sample_rate={out_sr}, expected {sample_rate}")

    y = np.asarray(y, dtype=np.float32).reshape(-1)

    # Keep length stable for downstream timestamp assumptions.
    if y.size > x.size:
        y = y[: x.size]
    elif y.size < x.size:
        y = np.pad(y, (0, x.size - y.size), mode="constant")

    return y

