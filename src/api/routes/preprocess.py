"""Audio preprocessing API helpers (capabilities/status).

This module intentionally keeps the surface small:
- ClearVoice integration is optional and only used when requested via
  `asr_options.preprocess.denoise_backend=clearvoice`.
- Frontend can query this endpoint to decide whether to show/enable the toggle.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from fastapi.responses import Response

from src.config import settings
from src.api.dependencies import process_audio_file
from src.api.asr_options import parse_asr_options
from src.models.backends.remote_utils import pcm16le_to_wav_bytes

router = APIRouter(prefix="/api/v1", tags=["preprocess"])


def _try_import(module: str) -> Tuple[bool, Optional[str]]:
    try:
        importlib.import_module(module)
        return True, None
    except Exception as e:
        msg = str(e).strip()
        return False, msg or f"failed to import {module}"


def _try_import_clearvoice(studio_dir: str) -> Tuple[bool, Optional[str]]:
    ok, err = _try_import("clearvoice")
    if ok:
        return True, None

    p = Path(str(studio_dir or "")).expanduser()
    if p.exists() and p.is_dir():
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
        ok2, err2 = _try_import("clearvoice")
        if ok2:
            return True, None
        return False, err2 or err

    # Studio dir missing: keep the original import error as the message.
    return False, err


@router.get("/preprocess/status")
async def get_preprocess_status() -> Dict[str, Any]:
    """Return best-effort availability of optional preprocessing backends."""
    clearvoice_enabled = bool(getattr(settings, "clearvoice_enable", True))
    clearvoice_service_base_url = str(getattr(settings, "clearvoice_service_base_url", "") or "").strip().rstrip("/")
    studio_dir = str(getattr(settings, "clearvoice_studio_dir", "") or "")
    studio_exists = False
    if studio_dir:
        try:
            p = Path(studio_dir).expanduser()
            studio_exists = p.exists() and p.is_dir()
        except Exception:
            studio_exists = False

    clearvoice_ok = False
    clearvoice_err: Optional[str] = None
    if clearvoice_enabled:
        if clearvoice_service_base_url:
            # Probe the dedicated ClearVoice service instead of importing locally.
            try:
                import httpx

                timeout_s = float(getattr(settings, "clearvoice_service_health_timeout_s", 2.0) or 2.0)
                if timeout_s <= 0:
                    timeout_s = 2.0
                async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                    resp = await client.get(f"{clearvoice_service_base_url}/health")
                    if 200 <= int(resp.status_code) < 300:
                        clearvoice_ok, clearvoice_err = True, None
                    else:
                        clearvoice_ok = False
                        clearvoice_err = f"service unhealthy (HTTP {resp.status_code})"
            except Exception as e:
                clearvoice_ok = False
                clearvoice_err = f"service probe failed: {e}"
        else:
            clearvoice_ok, clearvoice_err = _try_import_clearvoice(studio_dir)
    else:
        clearvoice_ok = False
        clearvoice_err = "disabled (CLEARVOICE_ENABLE=false)"

    deepfilter_ok, deepfilter_err = _try_import("deepfilternet")
    # Note: DeepFilterNet v3 shares the same import, but may require different checkpoints.
    deepfilter3_ok, deepfilter3_err = deepfilter_ok, deepfilter_err

    noisereduce_ok, noisereduce_err = _try_import("noisereduce")

    return {
        "code": 0,
        "preprocess": {
            "clearvoice": {
                "enabled": clearvoice_enabled,
                "mode": "remote" if bool(clearvoice_service_base_url) else "local",
                "available": bool(clearvoice_ok),
                "service_base_url": clearvoice_service_base_url,
                "studio_dir": studio_dir,
                "studio_dir_exists": bool(studio_exists),
                "error": clearvoice_err,
            },
            "deepfilter": {"available": bool(deepfilter_ok), "error": deepfilter_err},
            "deepfilter3": {"available": bool(deepfilter3_ok), "error": deepfilter3_err},
            "noisereduce": {"available": bool(noisereduce_ok), "error": noisereduce_err},
        },
    }


@router.post("/preprocess/enhance")
async def preprocess_enhance(
    file: UploadFile = File(..., description="音频文件（任意 FFmpeg 支持的格式）"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON（仅使用 preprocess 段）"),
) -> Response:
    """对音频做预处理并返回增强后的 WAV（16kHz mono PCM16）。

    典型用途：前端勾选 ClearVoice 降噪后下载“降噪后的音频”用于复核/归档。

    注意：
    - 该接口会解码并在内存中处理整段音频，适合“中短音频”。超长音频建议走异步链路。
    - 处理逻辑与 `/api/v1/transcribe` 使用同一套 `process_audio_file()` 预处理。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="请上传音频文件")

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    preprocess_options = (parsed_asr_options or {}).get("preprocess")

    pcm16le_bytes: Optional[bytes] = None
    async for audio_bytes in process_audio_file(file, preprocess_options=preprocess_options):
        pcm16le_bytes = audio_bytes
        break

    if not pcm16le_bytes:
        raise HTTPException(status_code=400, detail="音频文件为空或无法解码")

    wav_bytes = pcm16le_to_wav_bytes(pcm16le_bytes)

    try:
        stem = Path(str(file.filename)).stem or "audio"
    except Exception:
        stem = "audio"
    out_name = f"{stem}.enhanced.wav"

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
        },
    )
