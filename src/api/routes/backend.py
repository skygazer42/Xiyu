"""Backend info API (capabilities discovery).

Used by the frontend for multi-container / multi-port deployments to probe:
- which backend is configured for this Xiyu instance
- whether speaker diarization is supported
"""

import logging

from fastapi import APIRouter

from src.api.schemas import BackendCapabilities, BackendInfoResponse
from src.config import settings
import src.core.engine as engine_mod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["backend"])


def _cap_bool(value: object) -> bool:
    # Many backends implement supports_* as bool properties. In tests we often use
    # MagicMock, which should not accidentally evaluate to True.
    return value if isinstance(value, bool) else False


@router.get("/backend", response_model=BackendInfoResponse)
async def get_backend_info() -> BackendInfoResponse:
    backend = engine_mod.model_manager.backend

    info = {}
    try:
        raw_info = backend.get_info()
        if isinstance(raw_info, dict):
            info = raw_info
    except Exception as e:
        logger.warning(f"Failed to read backend.get_info(): {e}")

    supports_speaker = _cap_bool(getattr(backend, "supports_speaker", False))
    supports_speaker_external = bool(getattr(settings, "speaker_external_diarizer_enable", False)) and bool(
        str(getattr(settings, "speaker_external_diarizer_base_url", "")).strip()
    )
    supports_speaker_fallback = bool(getattr(settings, "speaker_fallback_diarization_enable", False)) and bool(
        str(getattr(settings, "speaker_fallback_diarization_base_url", "")).strip()
    )

    if supports_speaker_external:
        speaker_strategy = "external"
    elif supports_speaker:
        speaker_strategy = "native"
    elif supports_speaker_fallback:
        speaker_strategy = "fallback_diarization"
    else:
        behavior = settings.speaker_unsupported_behavior_effective
        if behavior == "ignore":
            speaker_strategy = "ignore"
        elif behavior == "error":
            speaker_strategy = "error"
        else:
            speaker_strategy = "fallback_backend"

    capabilities = BackendCapabilities(
        supports_speaker=supports_speaker,
        supports_streaming=_cap_bool(getattr(backend, "supports_streaming", False)),
        supports_hotwords=_cap_bool(getattr(backend, "supports_hotwords", False)),
        supports_speaker_fallback=supports_speaker_fallback and (supports_speaker is False),
        supports_speaker_external=supports_speaker_external,
        speaker_strategy=speaker_strategy,
    )

    return BackendInfoResponse(
        backend=settings.asr_backend,
        info=info,
        capabilities=capabilities,
        speaker_unsupported_behavior=settings.speaker_unsupported_behavior_effective,
    )
