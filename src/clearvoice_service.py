"""ClearVoice microservice (speech enhancement / denoise).

This service hosts the ClearerVoice-Studio model in a dedicated container.
Xiyu ASR backends can call it via `CLEARVOICE_SERVICE_BASE_URL` to avoid
initializing ClearVoice inside each ASR container.

API:
  - GET  /health
  - GET  /info
  - POST /api/v1/enhance  (multipart: file=@audio)
      -> returns audio/wav (16kHz, mono, PCM16)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import aiofiles
import ffmpeg
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from src.config import settings
from src.core.audio.clearvoice_denoise import (
    ClearVoiceDisabled,
    ClearVoiceNotAvailable,
    clearvoice_enhance,
)
from src.core.audio.pcm import float32_to_pcm16le_bytes, pcm16le_bytes_to_float32
from src.models.backends.remote_utils import pcm16le_to_wav_bytes

logger = logging.getLogger(__name__)

app = FastAPI(title="Xiyu ClearVoice Service", version=str(getattr(settings, "version", "0.1.0")))


def _truthy(v: str) -> bool:
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


async def _save_upload_file(upload: UploadFile, dest_path: Path, *, chunk_size: int = 1024 * 1024) -> None:
    """Save an UploadFile to disk in chunks (avoid reading whole file into memory)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest_path, "wb") as out:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            await out.write(chunk)


def _convert_path_to_pcm16le_bytes(input_path: str) -> bytes:
    """Convert any audio/video file to 16kHz mono PCM16LE bytes (s16le)."""
    audio_bytes, _ = (
        ffmpeg.input(str(input_path), threads=0)
        .output("-", format="s16le", acodec="pcm_s16le", ac=1, ar=16000)
        .run(cmd=["ffmpeg", "-nostdin"], capture_stdout=True, capture_stderr=True)
    )
    return audio_bytes


@app.on_event("startup")
def _maybe_warmup() -> None:
    # Keep startup fast by default. If enabled, this may trigger model weight downloads.
    if not _truthy(os.environ.get("CLEARVOICE_WARMUP_ON_STARTUP", "false")):
        return
    try:
        dummy = np.zeros((16000,), dtype=np.float32)
        _ = clearvoice_enhance(dummy, sample_rate=16000)
        logger.info("ClearVoice warmup completed")
    except Exception as e:
        logger.warning("ClearVoice warmup failed (ignored): %s", e)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "clearvoice"}


@app.get("/info")
def info() -> dict:
    payload = {
        "service": "clearvoice",
        "enabled": bool(getattr(settings, "clearvoice_enable", True)),
        "model": str(getattr(settings, "clearvoice_model", "FRCRN_SE_16K") or "FRCRN_SE_16K"),
        "force_cpu": bool(getattr(settings, "clearvoice_force_cpu", True)),
        "studio_dir": str(getattr(settings, "clearvoice_studio_dir", "") or ""),
        "device": str(getattr(settings, "device", "cpu") or "cpu"),
        "ngpu": int(getattr(settings, "ngpu", 0) or 0),
        "chunk_duration_s": float(getattr(settings, "clearvoice_chunk_duration_s", 30.0) or 30.0),
        "overlap_duration_s": float(getattr(settings, "clearvoice_overlap_duration_s", 0.5) or 0.5),
        "max_duration_s": float(getattr(settings, "clearvoice_service_max_duration_s", 600.0) or 600.0),
    }

    # Best-effort GPU visibility diagnostics.
    try:
        import torch

        payload["torch"] = str(getattr(torch, "__version__", "") or "")
        payload["cuda_available"] = bool(torch.cuda.is_available())
        payload["cuda_device_count"] = int(torch.cuda.device_count())
        if torch.cuda.is_available():
            try:
                payload["cuda_name0"] = str(torch.cuda.get_device_name(0))
            except Exception:
                pass
    except Exception as e:
        payload["cuda_available"] = False
        payload["cuda_error"] = str(e)

    return payload


@app.post("/api/v1/enhance")
async def enhance(file: UploadFile = File(..., description="Audio file to enhance (any format supported by FFmpeg)")):
    """Enhance audio via ClearVoice and return a 16kHz mono WAV."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")

    suffix = Path(file.filename).suffix if file.filename else ".wav"
    if not suffix:
        suffix = ".wav"

    temp_path = settings.uploads_dir / f"clearvoice_{os.urandom(8).hex()}{suffix}"
    try:
        await _save_upload_file(file, temp_path)

        # FFmpeg decode + model inference are blocking; run in a thread so health probes
        # and other lightweight endpoints don't get stuck behind a long enhance call.
        def _do_enhance(path: str) -> bytes:
            try:
                pcm16le = _convert_path_to_pcm16le_bytes(path)
            except ffmpeg.Error as e:
                detail = e.stderr.decode() if getattr(e, "stderr", None) else str(e)
                raise HTTPException(status_code=400, detail=f"ffmpeg decode failed: {detail}")

            audio = pcm16le_bytes_to_float32(pcm16le)
            duration_s = float(len(audio)) / 16000.0 if len(audio) else 0.0
            try:
                max_dur = float(getattr(settings, "clearvoice_service_max_duration_s", 600.0) or 0.0)
            except Exception:
                max_dur = 600.0

            if max_dur > 0 and duration_s > max_dur:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"audio too long for ClearVoice service: {duration_s:.1f}s > {max_dur:.1f}s. "
                        "Use Router long-audio chunking so ClearVoice runs per-chunk."
                    ),
                )

            try:
                enhanced = clearvoice_enhance(audio, sample_rate=16000)
            except (ClearVoiceDisabled, ClearVoiceNotAvailable) as e:
                raise HTTPException(status_code=503, detail=str(e))
            except Exception as e:
                logger.error("ClearVoice enhance failed: %s", e, exc_info=True)
                raise HTTPException(status_code=500, detail=f"clearvoice enhance failed: {e}")

            out_pcm = float32_to_pcm16le_bytes(enhanced)
            return pcm16le_to_wav_bytes(out_pcm, sample_rate=16000, channels=1, sampwidth=2)

        out_wav = await run_in_threadpool(_do_enhance, str(temp_path))
        return Response(content=out_wav, media_type="audio/wav")
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
