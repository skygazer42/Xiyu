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

from fastapi import APIRouter

from src.config import settings

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
                "available": bool(clearvoice_ok),
                "studio_dir": studio_dir,
                "studio_dir_exists": bool(studio_exists),
                "error": clearvoice_err,
            },
            "deepfilter": {"available": bool(deepfilter_ok), "error": deepfilter_err},
            "deepfilter3": {"available": bool(deepfilter3_ok), "error": deepfilter3_err},
            "noisereduce": {"available": bool(noisereduce_ok), "error": noisereduce_err},
        },
    }

