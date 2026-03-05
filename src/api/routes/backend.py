"""Backend info API (capabilities discovery).

Used by the frontend for multi-container / multi-port deployments to probe:
- which backend is configured for this Xiyu instance
- whether speaker diarization is supported
"""

import logging

from fastapi import APIRouter

from src.api.schemas import BackendCapabilities, BackendInfoResponse, BackendTargetsResponse, BackendTargetStatus
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


@router.get("/backend/targets", response_model=BackendTargetsResponse)
async def get_backend_targets() -> BackendTargetsResponse:
    """List router backend targets and best-effort probe their availability.

    For non-router deployments this returns an empty list.
    """
    backend = engine_mod.model_manager.backend
    backend_name = settings.asr_backend

    try:
        from src.models.backends.router import RouterBackend

        if not isinstance(backend, RouterBackend):
            return BackendTargetsResponse(code=0, backend=backend_name, targets=[])
    except Exception:
        return BackendTargetsResponse(code=0, backend=backend_name, targets=[])

    # Router targets are typically remote/proxy backends. Probing should be lightweight.
    router_backend = backend
    targets = dict(getattr(router_backend, "targets", {}) or {})

    # Include short/long as special entries to help debugging deployments.
    try:
        targets.setdefault("short", getattr(router_backend, "short_backend"))
    except Exception:
        pass
    try:
        targets.setdefault("long", getattr(router_backend, "long_backend"))
    except Exception:
        pass

    import asyncio

    try:
        import httpx
    except Exception as e:
        logger.warning("httpx is not available; skipping router target probes: %s", e)
        out = []
        for key, b in targets.items():
            info = {}
            try:
                raw = b.get_info()
                if isinstance(raw, dict):
                    info = raw
            except Exception:
                info = {}
            out.append(BackendTargetStatus(key=str(key), ok=False, info=info, error="httpx not installed"))
        return BackendTargetsResponse(code=0, backend=backend_name, targets=out)

    timeout_s = 1.5

    def _pick_probe_url(b, info: dict) -> str:
        base_url = ""
        try:
            base_url = str(getattr(b, "base_url", "") or "").rstrip("/")
        except Exception:
            base_url = ""
        if not base_url:
            return ""
        b_type = str(info.get("type") or "").strip().lower()
        if b_type == "xiyu_proxy":
            return f"{base_url}/health"
        # Remote OpenAI-compatible servers: probe /v1/models.
        return f"{base_url}/v1/models"

    async def _probe_one(key: str, b) -> BackendTargetStatus:
        info: dict = {}
        try:
            raw = b.get_info()
            if isinstance(raw, dict):
                info = raw
        except Exception as e:
            info = {}
            return BackendTargetStatus(key=key, ok=False, info=info, error=f"get_info failed: {e}")

        url = _pick_probe_url(b, info)
        if not url:
            # Local backend or missing base_url; treat as available only if it's in-process.
            # For router deployments we prefer conservative behavior: mark missing URL as not ok.
            return BackendTargetStatus(key=key, ok=False, info=info, error="missing probe url")

        try:
            async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            return BackendTargetStatus(key=key, ok=True, info=info, error=None)
        except Exception as e:
            msg = str(e).strip()
            if len(msg) > 200:
                msg = msg[:200] + " ..."
            return BackendTargetStatus(key=key, ok=False, info=info, error=msg or "probe failed")

    # Probe in parallel; keep ordering stable for UI display.
    keys = sorted([str(k) for k in targets.keys()])
    tasks = []
    for key in keys:
        b = targets.get(key)
        if b is None:
            tasks.append(asyncio.sleep(0, result=BackendTargetStatus(key=key, ok=False, info={}, error="missing")))
        else:
            tasks.append(_probe_one(key, b))
    results = await asyncio.gather(*tasks)

    return BackendTargetsResponse(code=0, backend=backend_name, targets=results)
