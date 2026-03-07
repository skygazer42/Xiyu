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

    url = f"{base}/api/v1/enhance"

    def _enhance_once(client: httpx.Client, chunk: np.ndarray) -> np.ndarray:
        pcm = float32_to_pcm16le_bytes(chunk)
        wav = pcm16le_to_wav_bytes(pcm, sample_rate=sample_rate, channels=1, sampwidth=2)
        files = {"file": ("audio.wav", wav, "audio/wav")}

        try:
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
        if y.size > chunk.size:
            y = y[: chunk.size]
        elif y.size < chunk.size:
            y = np.pad(y, (0, chunk.size - y.size), mode="constant")
        return y

    duration_s = float(x.size) / float(sample_rate)
    try:
        max_dur = float(getattr(settings, "clearvoice_service_max_duration_s", 600.0) or 0.0)
    except Exception:
        max_dur = 600.0
    if max_dur < 0:
        max_dur = 0.0

    try:
        chunk_duration_s = float(getattr(settings, "clearvoice_chunk_duration_s", 30.0) or 30.0)
    except Exception:
        chunk_duration_s = 30.0
    try:
        overlap_duration_s = float(getattr(settings, "clearvoice_overlap_duration_s", 0.5) or 0.5)
    except Exception:
        overlap_duration_s = 0.5

    if chunk_duration_s <= 0:
        chunk_duration_s = 30.0
    if overlap_duration_s < 0:
        overlap_duration_s = 0.0

    # When the audio is longer than the service max duration, we must chunk
    # client-side; otherwise the service will reject the request.
    #
    # NOTE: even if max_dur is set to "unlimited" (<=0), chunking extremely long
    # audio is still safer (avoids multi-GB multipart bodies).
    should_chunk = False
    if max_dur > 0 and duration_s > max_dur:
        should_chunk = True
    elif max_dur <= 0 and duration_s > max(600.0, chunk_duration_s * 10.0):
        should_chunk = True

    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            if not should_chunk:
                return _enhance_once(client, x)

            chunk_samples = int(round(chunk_duration_s * sample_rate))
            overlap_samples = int(round(overlap_duration_s * sample_rate))
            if chunk_samples <= 0:
                chunk_samples = int(sample_rate * 30)
            if overlap_samples < 0:
                overlap_samples = 0
            if overlap_samples >= chunk_samples:
                # Keep progress moving; avoid zero/negative hop.
                overlap_samples = max(0, chunk_samples // 4)

            hop = max(1, chunk_samples - overlap_samples)
            if hop <= 0:
                hop = max(1, chunk_samples)

            logger.info(
                "ClearVoice service: chunking %ss audio (chunk=%ss, overlap=%ss, max=%ss)",
                f"{duration_s:.1f}",
                f"{chunk_duration_s:.1f}",
                f"{overlap_duration_s:.2f}",
                f"{max_dur:.0f}" if max_dur > 0 else "unlimited",
            )

            out = np.zeros((x.size,), dtype=np.float32)
            n = x.size
            fade = None
            if overlap_samples > 0:
                fade = np.linspace(0.0, 1.0, overlap_samples, dtype=np.float32)

            start = 0
            chunk_index = 0
            while start < n:
                end = min(n, start + chunk_samples)
                chunk = x[start:end]
                y = _enhance_once(client, chunk)

                if chunk_index == 0 or overlap_samples <= 0 or fade is None:
                    out[start:end] = y
                else:
                    fade_len = min(overlap_samples, y.size, max(0, n - start))
                    if fade_len > 0:
                        f = fade[:fade_len]
                        out[start : start + fade_len] = out[start : start + fade_len] * (1.0 - f) + y[:fade_len] * f
                        out[start + fade_len : end] = y[fade_len:]
                    else:
                        out[start:end] = y

                chunk_index += 1
                start += hop

            return out
    except Exception:
        # Keep the original exception semantics: caller requested ClearVoice,
        # so we fail fast instead of silently falling back.
        raise
