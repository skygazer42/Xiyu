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
from src.core.audio.clearvoice_utils import (
    clearvoice_model_sample_rate,
    normalize_clearvoice_model_name,
    resample_audio,
)

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

    @staticmethod
    def _resolve_checkpoint_dir(model_name: str) -> Path:
        """Resolve ClearVoice checkpoint directory for `model_name`.

        ClearerVoice-Studio uses relative paths like `checkpoints/FRCRN_SE_16K` by
        default. In our Docker deployment we mount a persistent directory at
        `/app/checkpoints`, so prefer it when present.
        """
        name = normalize_clearvoice_model_name(str(model_name or "").strip() or "FRCRN_SE_16K")
        docker_dir = Path("/app/checkpoints")
        if docker_dir.exists() and docker_dir.is_dir():
            return docker_dir / name
        return Path("checkpoints") / name

    def _ensure_checkpoints(self, model_name: str) -> None:
        """Best-effort: ensure ClearVoice checkpoint files exist.

        The upstream ClearerVoice-Studio helper may leave a partial checkout when
        `git-lfs` is unavailable, which causes `last_best_checkpoint.pt` to be
        missing even though `last_best_checkpoint` exists.
        """
        model_name = normalize_clearvoice_model_name(str(model_name or "").strip() or "FRCRN_SE_16K")
        ckpt_dir = self._resolve_checkpoint_dir(model_name)
        best_file = ckpt_dir / "last_best_checkpoint"

        need_download = False
        if not best_file.exists():
            need_download = True
        else:
            try:
                refs = [line.strip() for line in best_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            except Exception:
                refs = []
            if not refs:
                need_download = True
            else:
                for ref in refs:
                    p = ckpt_dir / ref
                    if not p.exists():
                        need_download = True
                        break
                    # Guard against LFS pointer files (a few hundred bytes) being mistaken for weights.
                    try:
                        if p.is_file() and p.stat().st_size < 1024:
                            need_download = True
                            break
                    except Exception:
                        pass

        if not need_download:
            return

        repo_id = f"alibabasglab/{model_name}"
        logger.info("ClearVoice checkpoint missing; downloading from HuggingFace: %s -> %s", repo_id, ckpt_dir)

        try:
            from huggingface_hub import snapshot_download
        except Exception as e:
            raise ClearVoiceNotAvailable(
                "huggingface_hub is required to auto-download ClearVoice checkpoints. "
                "Install it or manually place the weights under checkpoints/."
            ) from e

        ckpt_dir.mkdir(parents=True, exist_ok=True)
        # Use the hub cache by default; `local_dir` provides a stable layout for ClearVoice code.
        snapshot_download(repo_id=repo_id, local_dir=str(ckpt_dir))

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

            # Ensure checkpoint files exist before ClearVoice tries to load them.
            requested_model = str(self._cfg.model or "").strip() or "FRCRN_SE_16K"
            model_name = normalize_clearvoice_model_name(requested_model)
            self._ensure_checkpoints(model_name)

            ClearVoice = self._import_clearvoice()
            cv = ClearVoice(task="speech_enhancement", model_names=[model_name])

            try:
                model = cv.models[0]
            except Exception as e:
                raise RuntimeError(f"ClearVoice model init failed: {e}") from e

            # Device placement:
            # - When force_cpu=true: keep ClearVoice on CPU (safe default to avoid stealing VRAM).
            # - Otherwise: follow `settings.device` if possible (cuda/cpu).
            try:
                import torch

                want_cuda = (
                    (not bool(self._cfg.force_cpu))
                    and str(getattr(settings, "device", "cpu") or "cpu").strip().lower() == "cuda"
                    and int(getattr(settings, "ngpu", 0) or 0) > 0
                )

                if want_cuda and torch.cuda.is_available():
                    model.model.to(torch.device("cuda"))
                    model.device = torch.device("cuda")
                    try:
                        model.args.use_cuda = 1
                    except Exception:
                        pass
                else:
                    model.model.to(torch.device("cpu"))
                    model.device = torch.device("cpu")
                    try:
                        model.args.use_cuda = 0
                    except Exception:
                        pass
            except Exception as e:
                # Do not fail service startup on best-effort device moves.
                logger.warning("ClearVoice device placement failed (ignored): %s", e)

            self._speech_model = model
            logger.info(
                "ClearVoice denoiser ready: model=%s (requested=%s) force_cpu=%s studio_dir=%s",
                model_name,
                requested_model,
                self._cfg.force_cpu,
                self._cfg.studio_dir,
            )

    def enhance(self, audio: np.ndarray, *, sample_rate: int) -> np.ndarray:
        """Enhance 1D float32 audio in [-1, 1]."""
        self._ensure_loaded()
        if self._speech_model is None:
            return audio

        if audio.size == 0:
            return audio

        model_name = normalize_clearvoice_model_name(str(self._cfg.model or "").strip() or "FRCRN_SE_16K")
        model_sr = int(clearvoice_model_sample_rate(model_name))
        in_sr = int(sample_rate)

        x = np.asarray(audio, dtype=np.float32).reshape(-1)

        chunk_duration_s = float(self._cfg.chunk_duration_s or 0.0)
        overlap_duration_s = float(self._cfg.overlap_duration_s or 0.0)
        if chunk_duration_s <= 0.0:
            chunk_duration_s = 30.0
        if overlap_duration_s < 0.0:
            overlap_duration_s = 0.0
        if overlap_duration_s >= chunk_duration_s:
            overlap_duration_s = min(0.5, max(0.0, chunk_duration_s / 4.0))

        chunk_samples = int(chunk_duration_s * in_sr)
        overlap_samples = int(overlap_duration_s * in_sr)
        if chunk_samples <= 0:
            chunk_samples = len(x)
        if overlap_samples < 0:
            overlap_samples = 0
        if overlap_samples >= chunk_samples:
            overlap_samples = 0

        def _run_segment(seg: np.ndarray) -> np.ndarray:
            """Run ClearVoice on a single segment and keep length stable."""
            seg_in = np.asarray(seg, dtype=np.float32).reshape(-1)
            if seg_in.size == 0:
                return seg_in

            # Resample to model SR if needed.
            if in_sr != model_sr:
                seg_model = resample_audio(seg_in, sr_from=in_sr, sr_to=model_sr)
            else:
                seg_model = seg_in

            with _infer_lock:
                out = self._speech_model.decode_data(seg_model.reshape(1, -1))

            y_model = np.asarray(out, dtype=np.float32)
            if y_model.ndim == 2:
                y_model = y_model[0]

            # Resample back to input SR if needed.
            if in_sr != model_sr:
                y = resample_audio(y_model, sr_from=model_sr, sr_to=in_sr)
            else:
                y = y_model

            y = np.asarray(y, dtype=np.float32).reshape(-1)

            # Keep length stable for downstream timestamp assumptions.
            if y.size > seg_in.size:
                y = y[: seg_in.size]
            elif y.size < seg_in.size:
                y = np.pad(y, (0, seg_in.size - y.size), mode="constant")

            return y

        # Small audio: run once.
        if len(x) <= chunk_samples:
            y = _run_segment(x)
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
            min_len = max(1024, int(0.1 * in_sr))
            if seg.size < min_len:
                seg_in = np.pad(seg, (0, min_len - seg.size), mode="constant")
                trim_len = seg.size
            else:
                seg_in = seg
                trim_len = seg.size

            seg_out = _run_segment(seg_in)
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
