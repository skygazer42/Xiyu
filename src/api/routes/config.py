# coding: utf-8
"""配置管理 API"""

import logging
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])

_SENSITIVE_SUFFIXES = ("_api_key", "_access_token", "_refresh_token", "_secret", "_password")
_SENSITIVE_KEYS = {
    "api_key",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "hf_token",
}


def _is_sensitive_key(key: str) -> bool:
    k = str(key or "").strip().lower()
    if not k:
        return False
    if k in _SENSITIVE_KEYS:
        return True
    return any(k.endswith(suf) for suf in _SENSITIVE_SUFFIXES)


def _redact_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Redact secrets from a settings dict before returning it to clients."""
    out: Dict[str, Any] = {}
    for k, v in (config or {}).items():
        if _is_sensitive_key(k):
            # Preserve emptiness vs. set-ness for debugging, but never leak the value.
            out[k] = "<redacted>" if v not in (None, "", False) else v
        else:
            out[k] = v
    return out


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    updates: Dict[str, Any]


class ConfigResponse(BaseModel):
    """配置响应"""
    config: Dict[str, Any]


# 允许运行时修改的配置项
MUTABLE_CONFIG_KEYS = {
    # 纠错相关
    "text_correct_enable",
    "text_correct_backend",
    "correction_pipeline",
    "confidence_threshold",
    "confidence_fallback",
    # 热词相关
    "hotwords_threshold",
    "hotword_injection_enable",
    "hotword_injection_max",
    # LLM 相关
    "llm_enable",
    "llm_role",
    "llm_fulltext_enable",
    "llm_batch_size",
    "llm_context_sentences",
    # 会议概览（LLM summarization）
    "meeting_overview_enable",
    # 文本后处理
    "filler_remove_enable",
    "filler_aggressive",
    "qj2bj_enable",
    "itn_enable",
    "itn_erhua_remove",
    "spacing_cjk_ascii_enable",
    "spoken_punc_enable",
    "acronym_merge_enable",
    "gov_format_enable",
    "zh_convert_enable",
    "zh_convert_locale",
    "punc_convert_enable",
    "punc_add_space",
    "punc_restore_enable",
    "punc_restore_model",
    "punc_merge_enable",
    "trash_punc_enable",
    "trash_punc_chars",
    # 音频预处理
    "audio_normalize_enable",
    "audio_denoise_enable",
    "audio_denoise_backend",
    "audio_vocal_separate_enable",
    "audio_trim_silence_enable",
}


def get_current_config() -> Dict[str, Any]:
    """获取当前可变配置"""
    return {
        key: getattr(settings, key)
        for key in MUTABLE_CONFIG_KEYS
        if hasattr(settings, key)
    }


@router.get("", response_model=ConfigResponse)
async def get_config():
    """获取当前配置

    返回所有可运行时修改的配置项及其当前值。
    """
    return ConfigResponse(config=get_current_config())


@router.get("/all")
async def get_all_config():
    """获取完整配置（包括只读项）

    返回所有配置项，包括服务启动后不可修改的项。
    """
    # Avoid iterating over `dir(settings)`, which includes many Pydantic internals
    # (e.g. `model_fields`) that are not JSON-serializable and can crash this endpoint.
    config = _redact_config(settings.model_dump(mode="json"))

    # Expose helpful computed properties explicitly.
    config["speaker_unsupported_behavior_effective"] = settings.speaker_unsupported_behavior_effective

    return {"config": config, "mutable_keys": sorted(MUTABLE_CONFIG_KEYS)}


@router.post("", response_model=ConfigResponse)
async def update_config(request: ConfigUpdateRequest):
    """更新配置

    运行时更新配置项。仅支持 MUTABLE_CONFIG_KEYS 中的配置项。

    Args:
        request: 包含要更新的配置键值对

    Returns:
        更新后的配置
    """
    updates = request.updates
    updated = []
    rejected = []

    for key, value in updates.items():
        if key not in MUTABLE_CONFIG_KEYS:
            rejected.append(key)
            continue

        if not hasattr(settings, key):
            rejected.append(key)
            continue

        try:
            # 类型检查
            current_value = getattr(settings, key)
            if current_value is not None and type(value) != type(current_value):
                # 尝试类型转换
                value = type(current_value)(value)

            setattr(settings, key, value)
            updated.append(key)
            logger.info(f"Config updated: {key} = {value}")
        except Exception as e:
            logger.warning(f"Failed to update {key}: {e}")
            rejected.append(key)

    if rejected:
        logger.warning(f"Rejected config updates: {rejected}")

    updated_set = set(updated)

    # Apply runtime side-effects to the in-process engine, so that updating
    # config from the frontend takes effect immediately.
    try:
        from src.core.engine import transcription_engine
    except Exception as e:
        logger.error(f"Failed to import transcription_engine for runtime update: {e}")
        transcription_engine = None  # type: ignore[assignment]

    if transcription_engine is not None:
        # Text corrector toggles/backends (lazy init)
        if {"text_correct_enable", "text_correct_backend"} & updated_set:
            try:
                transcription_engine._text_correct_enabled = bool(settings.text_correct_enable)
                transcription_engine._text_corrector = None
                logger.info("Transcription engine text corrector settings applied")
            except Exception as e:
                logger.error(f"Failed to apply text corrector settings: {e}")

        # Hotword threshold should apply to corrector immediately.
        if {"hotwords_threshold"} & updated_set:
            try:
                th = float(settings.hotwords_threshold)
                transcription_engine.corrector.threshold = th
                transcription_engine.corrector.similar_threshold = th - 0.2
                transcription_engine.corrector.fast_rag.threshold = min(
                    transcription_engine.corrector.threshold,
                    transcription_engine.corrector.similar_threshold,
                ) - 0.1
                logger.info("Transcription engine hotword threshold applied")
            except Exception as e:
                logger.error(f"Failed to apply hotword threshold: {e}")

        # Post-processor settings: rebuild the processor instance.
        postprocess_keys = {
            "filler_remove_enable",
            "filler_aggressive",
            "qj2bj_enable",
            "itn_enable",
            "itn_erhua_remove",
            "spacing_cjk_ascii_enable",
            "spoken_punc_enable",
            "acronym_merge_enable",
            "gov_format_enable",
            "zh_convert_enable",
            "zh_convert_locale",
            "punc_convert_enable",
            "punc_add_space",
            "punc_restore_enable",
            "punc_restore_model",
            "punc_merge_enable",
            "trash_punc_enable",
            "trash_punc_chars",
        }
        if postprocess_keys & updated_set:
            try:
                from src.core.text_processor import TextPostProcessor

                transcription_engine.post_processor = TextPostProcessor.from_config(settings)
                logger.info("Transcription engine post-processor reloaded")
            except Exception as e:
                logger.error(f"Failed to reload post-processor: {e}")

    return ConfigResponse(config=get_current_config())


@router.post("/reload")
async def reload_engine():
    """重新加载引擎

    重新初始化转写引擎的各组件，应用最新配置。
    """
    try:
        from src.core.engine import transcription_engine
        from src.core.text_processor import TextPostProcessor

        # 重新加载后处理器
        transcription_engine.post_processor = TextPostProcessor.from_config(settings)

        # 重新加载热词
        transcription_engine.load_all()

        logger.info("Transcription engine reloaded")
        return {"status": "success", "message": "Engine reloaded"}
    except Exception as e:
        logger.error(f"Failed to reload engine: {e}")
        raise HTTPException(status_code=500, detail=str(e))
