"""ClearVoice (ClearerVoice-Studio) speech enhancement wrapper.

This module is intentionally optional:
- If `clearvoice` is installed, we import it directly.
- Otherwise we can import from a local checkout via `settings.clearvoice_studio_dir`.

We run inference in chunks with overlap + crossfade to:
- avoid creating huge GPU tensors for multi-hour meetings
- reduce boundary artifacts
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from src.config import settings

logger = logging.getLogger(__name__)

_load_lock = threading.Lock()
_infer_lock = threading.Lock()


@dataclass(frozen=True)
class ClearVoiceConfig:
    model: str
    force_cpu: bool
    studio_dir: str
    chunk_duration_s: float
    overlap_duration_s: float


class ClearVoiceNotAvailable(RuntimeError):
    pass


class ClearVoiceDisabled(RuntimeError):
    pass


class ClearVoiceDenoiser:
    def __init__(self, cfg: ClearVoiceConfig):
        self._cfg = cfg
        self._speech_model = None

    def _import_clearvoice(self):
        try:
            from clearvoice import ClearVoice  # type: ignore
            return ClearVoice
        except Exception as e:
            studio_dir = str(self._cfg.studio_dir or "").strip()
            if studio_dir:
                p = Path(studio_dir).expanduser()
                if p.exists() and p.is_dir():
                    sys.path.insert(0, str(p))
                    try:
                        from clearvoice import ClearVoice  # type: ignore
                        return ClearVoice
                    except Exception:
                        pass

            raise ClearVoiceNotAvailable(
                "ClearVoice is not available. Install `clearvoice` or mount the "
                f"ClearerVoice-Studio checkout and set CLEARVOICE_STUDIO_DIR (current={studio_dir!r})."
            ) from e

    def _ensure_loaded(self) -> None:
        if self._speech_model is not None:
            return

        with _load_lock:
            if self._speech_model is not None:
                return

            if not bool(getattr(settings, "clearvoice_enable", True)):
                raise ClearVoiceDisabled("ClearVoice is disabled (set CLEARVOICE_ENABLE=true).")

            ClearVoice = self._import_clearvoice()
            cv = ClearVoice(task="speech_enhancement", model_names=[str(self._cfg.model)])

            try:
                model = cv.models[0]
            except Exception as e:
                raise RuntimeError(f"ClearVoice model init failed: {e}") from e

            # Best-effort: move to CPU to avoid stealing VRAM from ASR models.
            if bool(self._cfg.force_cpu):
                try:
                    import torch

                    model.model.to(torch.device("cpu"))
                    model.device = torch.device("cpu")
                    try:
                        model.args.use_cuda = 0
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning("ClearVoice force_cpu failed (ignored): %s", e)

            self._speech_model = model
            logger.info(
                "ClearVoice denoiser ready: model=%s force_cpu=%s studio_dir=%s",
                self._cfg.model,
                self._cfg.force_cpu,
                self._cfg.studio_dir,
            )

    def enhance(self, audio: np.ndarray, *, sample_rate: int) -> np.ndarray:
        """Enhance 1D float32 audio in [-1, 1]."""
        self._ensure_loaded()
        if self._speech_model is None:
            return audio

        if sample_rate != 16000:
            # Current ClearVoice integration is only validated for 16k enhancement models.
            logger.warning("ClearVoice denoise expects 16kHz audio; got %sHz, skipping", sample_rate)
            return audio

        if audio.size == 0:
            return audio

        x = np.asarray(audio, dtype=np.float32).reshape(-1)

        chunk_duration_s = float(self._cfg.chunk_duration_s or 0.0)
        overlap_duration_s = float(self._cfg.overlap_duration_s or 0.0)
        if chunk_duration_s <= 0.0:
            chunk_duration_s = 30.0
        if overlap_duration_s < 0.0:
            overlap_duration_s = 0.0
        if overlap_duration_s >= chunk_duration_s:
            overlap_duration_s = min(0.5, max(0.0, chunk_duration_s / 4.0))

        chunk_samples = int(chunk_duration_s * sample_rate)
        overlap_samples = int(overlap_duration_s * sample_rate)
        if chunk_samples <= 0:
            chunk_samples = len(x)
        if overlap_samples < 0:
            overlap_samples = 0
        if overlap_samples >= chunk_samples:
            overlap_samples = 0

        # Small audio: run once.
        if len(x) <= chunk_samples:
            with _infer_lock:
                out = self._speech_model.decode_data(x.reshape(1, -1))
            y = np.asarray(out, dtype=np.float32)
            if y.ndim == 2:
                y = y[0]
            return y[: len(x)]

        # Chunked enhancement with crossfade overlap.
        step = chunk_samples - overlap_samples
        if step <= 0:
            step = chunk_samples
            overlap_samples = 0

        y_full = np.zeros_like(x, dtype=np.float32)
        first = True
        pos = 0

        while pos < len(x):
            end = min(pos + chunk_samples, len(x))
            seg = x[pos:end]
            # Pad very short tail chunks for STFT stability, then trim back.
            min_len = max(1024, int(0.1 * sample_rate))
            if seg.size < min_len:
                seg_in = np.pad(seg, (0, min_len - seg.size), mode="constant")
                trim_len = seg.size
            else:
                seg_in = seg
                trim_len = seg.size

            with _infer_lock:
                out = self._speech_model.decode_data(seg_in.reshape(1, -1))
            seg_out = np.asarray(out, dtype=np.float32)
            if seg_out.ndim == 2:
                seg_out = seg_out[0]
            seg_out = seg_out[:trim_len]

            if first or overlap_samples <= 0:
                y_full[pos:end] = seg_out
                first = False
            else:
                overlap_end = min(pos + overlap_samples, end)
                ov_len = max(0, overlap_end - pos)
                if ov_len > 0:
                    fade_in = np.linspace(0.0, 1.0, ov_len, endpoint=False, dtype=np.float32)
                    fade_out = 1.0 - fade_in
                    y_full[pos:overlap_end] = y_full[pos:overlap_end] * fade_out + seg_out[:ov_len] * fade_in
                y_full[overlap_end:end] = seg_out[ov_len:]

            pos += step

        return y_full


_DENOISER: Optional[ClearVoiceDenoiser] = None


def get_clearvoice_denoiser() -> ClearVoiceDenoiser:
    global _DENOISER
    if _DENOISER is not None:
        return _DENOISER

    cfg = ClearVoiceConfig(
        model=str(getattr(settings, "clearvoice_model", "FRCRN_SE_16K") or "FRCRN_SE_16K"),
        force_cpu=bool(getattr(settings, "clearvoice_force_cpu", True)),
        studio_dir=str(getattr(settings, "clearvoice_studio_dir", "") or ""),
        chunk_duration_s=float(getattr(settings, "clearvoice_chunk_duration_s", 30.0) or 30.0),
        overlap_duration_s=float(getattr(settings, "clearvoice_overlap_duration_s", 0.5) or 0.5),
    )
    _DENOISER = ClearVoiceDenoiser(cfg)
    return _DENOISER


def clearvoice_enhance(audio: np.ndarray, *, sample_rate: int = 16000) -> np.ndarray:
    """Convenience wrapper used by AudioPreprocessor."""
    denoiser = get_clearvoice_denoiser()
    return denoiser.enhance(audio, sample_rate=sample_rate)
