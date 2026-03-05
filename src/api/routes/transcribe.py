"""转写 API 路由"""
import asyncio
import logging
import time
from typing import Optional, List
from fastapi import APIRouter, File, UploadFile, Form, HTTPException

from src.api.schemas import (
    TranscribeResponse, SentenceInfo, SpeakerTurn,
    BatchTranscribeResponse, BatchTranscribeItem,
    EnsembleTranscribeResponse,
)
from src.api.dependencies import process_audio_file
from src.api.asr_options import parse_asr_options
from src.core.engine import transcription_engine
from src.core.ensemble_v2 import transcribe_all_models
from src.utils.service_metrics import metrics
from src.utils.subtitles import generate_srt_from_result
from src.models.backends.remote_utils import pcm16le_to_wav_bytes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["transcribe"])

_ENSEMBLE_LLM_ROLES = {
    "policy_meeting",
    "policy_meeting_v2",
    "policy_meeting_aggressive",
}


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="音频文件"),
    with_speaker: bool = Form(default=False, description="是否进行说话人识别"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
    apply_llm: bool = Form(default=False, description="是否应用LLM润色"),
    llm_role: str = Form(
        default="default",
        description=(
            "LLM 角色（单模型润色）。推荐（政务/政策会议）："
            "policy_polish_strict / policy_polish_balanced / policy_polish_aggressive"
        ),
    ),
    include_srt: bool = Form(default=False, description="是否在响应中包含 SRT 字幕内容"),
    target_backend: Optional[str] = Form(
        default=None,
        description=(
            "Router 目标后端（单端口部署专用）：auto/qwen3/vibevoice/pytorch/onnx/sensevoice/gguf/whisper..."
        ),
    ),
    hotwords: Optional[str] = Form(default=None, description="额外热词 (空格分隔)"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON (per-request tuning)"),
):
    """
    上传音频文件进行转写

    支持的音频格式: wav, mp3, m4a, flac, ogg 等
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="请上传音频文件")

    metrics.increment_requests()

    if apply_llm and llm_role in _ENSEMBLE_LLM_ROLES:
        metrics.increment_failure()
        raise HTTPException(
            status_code=400,
            detail=(
                f"llm_role={llm_role!r} is an ensemble role for /api/v1/transcribe/all. "
                "For /api/v1/transcribe use: policy_polish_strict / policy_polish_balanced / policy_polish_aggressive."
            ),
        )

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        metrics.increment_failure()
        raise HTTPException(status_code=400, detail=str(e))

    t0 = time.time()
    try:
        preprocess_options = (parsed_asr_options or {}).get("preprocess")
        async for audio_bytes in process_audio_file(file, preprocess_options=preprocess_options):
            result = await transcription_engine.transcribe_auto_async(
                audio_bytes,
                with_speaker=with_speaker,
                apply_hotword=apply_hotword,
                apply_llm=apply_llm,
                llm_role=llm_role,
                hotwords=hotwords,
                asr_options=parsed_asr_options,
                target_backend=target_backend,
            )

            # 更新指标
            audio_duration = len(audio_bytes) / 2 / 16000  # 16bit, 16kHz
            metrics.add_audio_duration(audio_duration)
            metrics.add_processing_time(time.time() - t0)
            metrics.increment_success()

            srt: Optional[str] = None
            if include_srt:
                try:
                    srt = generate_srt_from_result(result)
                except Exception as e:
                    logger.warning("Generate SRT failed (ignored): %s", e)

            return TranscribeResponse(
                code=0,
                text=result["text"],
                text_accu=result.get("text_accu"),
                sentences=[SentenceInfo(**s) for s in result["sentences"]],
                speaker_turns=(
                    [SpeakerTurn(**t) for t in result.get("speaker_turns", [])]
                    if result.get("speaker_turns") is not None
                    else None
                ),
                transcript=result.get("transcript"),
                srt=srt,
                raw_text=result.get("raw_text"),
            )

    except ValueError as e:
        metrics.increment_failure()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        metrics.increment_failure()
        logger.error(f"Transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"转写失败: {str(e)}")


@router.post("/transcribe/batch", response_model=BatchTranscribeResponse)
async def transcribe_batch(
    files: List[UploadFile] = File(..., description="多个音频文件"),
    with_speaker: bool = Form(default=False, description="是否进行说话人识别"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
    apply_llm: bool = Form(default=False, description="是否应用LLM润色"),
    llm_role: str = Form(
        default="default",
        description=(
            "LLM 角色（单模型润色）。推荐（政务/政策会议）："
            "policy_polish_strict / policy_polish_balanced / policy_polish_aggressive"
        ),
    ),
    target_backend: Optional[str] = Form(
        default=None,
        description=(
            "Router 目标后端（单端口部署专用）：auto/qwen3/vibevoice/pytorch/onnx/sensevoice/gguf/whisper..."
        ),
    ),
    hotwords: Optional[str] = Form(default=None, description="额外热词"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON (per-request tuning)"),
    max_concurrent: int = Form(default=3, description="最大并发数"),
):
    """
    批量上传音频文件进行转写

    支持同时上传多个文件，并行处理。
    """
    if not files:
        raise HTTPException(status_code=400, detail="请上传至少一个音频文件")

    if apply_llm and llm_role in _ENSEMBLE_LLM_ROLES:
        metrics.increment_requests()
        metrics.increment_failure()
        raise HTTPException(
            status_code=400,
            detail=(
                f"llm_role={llm_role!r} is an ensemble role for /api/v1/transcribe/all. "
                "For /api/v1/transcribe/batch use: policy_polish_strict / policy_polish_balanced / policy_polish_aggressive."
            ),
        )

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        metrics.increment_requests()
        metrics.increment_failure()
        raise HTTPException(status_code=400, detail=str(e))

    results: List[BatchTranscribeItem] = []
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_single_file(index: int, file: UploadFile) -> BatchTranscribeItem:
        """处理单个文件"""
        async with semaphore:
            # Metrics semantics: count each file as a request, so batch mode matches
            # single-file mode.
            metrics.increment_requests()
            t0 = time.time()
            try:
                preprocess_options = (parsed_asr_options or {}).get("preprocess")
                async for audio_bytes in process_audio_file(file, preprocess_options=preprocess_options):
                    result = await transcription_engine.transcribe_auto_async(
                        audio_bytes,
                        with_speaker=with_speaker,
                        apply_hotword=apply_hotword,
                        apply_llm=apply_llm,
                        llm_role=llm_role,
                        hotwords=hotwords,
                        asr_options=parsed_asr_options,
                        target_backend=target_backend,
                    )

                    # 更新指标
                    audio_duration = len(audio_bytes) / 2 / 16000
                    metrics.add_audio_duration(audio_duration)
                    metrics.add_processing_time(time.time() - t0)
                    metrics.increment_success()

                    return BatchTranscribeItem(
                        index=index,
                        filename=file.filename or f"file_{index}",
                        success=True,
                        result=TranscribeResponse(
                            code=0,
                            text=result["text"],
                            text_accu=result.get("text_accu"),
                            sentences=[SentenceInfo(**s) for s in result["sentences"]],
                            speaker_turns=(
                                [SpeakerTurn(**t) for t in result.get("speaker_turns", [])]
                                if result.get("speaker_turns") is not None
                                else None
                            ),
                            transcript=result.get("transcript"),
                            raw_text=result.get("raw_text"),
                        ),
                    )
            except Exception as e:
                metrics.increment_failure()
                logger.error(f"Batch item {index} failed: {e}")
                return BatchTranscribeItem(
                    index=index,
                    filename=file.filename or f"file_{index}",
                    success=False,
                    error=str(e),
                )

    # 并行处理所有文件
    tasks = [process_single_file(i, f) for i, f in enumerate(files)]
    results = await asyncio.gather(*tasks)

    # 统计结果
    success_count = sum(1 for r in results if r.success)
    failed_count = len(results) - success_count

    return BatchTranscribeResponse(
        code=0 if failed_count == 0 else 1,
        total=len(files),
        success_count=success_count,
        failed_count=failed_count,
        results=results,
    )


@router.post("/transcribe/all", response_model=EnsembleTranscribeResponse)
async def transcribe_all_models_api(
    file: UploadFile = File(..., description="音频文件"),
    with_speaker: bool = Form(default=True, description="是否进行说话人识别（推荐开启）"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
    apply_llm: bool = Form(default=True, description="是否调用 LLM 进行多模型融合润色"),
    llm_role: str = Form(
        default="policy_meeting_aggressive",
        description="LLM 角色（政策会议多模型融合）：policy_meeting（严格）/policy_meeting_v2（平衡）/policy_meeting_aggressive（激进）",
    ),
    include_srt: bool = Form(default=True, description="是否在 final 中包含 SRT 字幕内容"),
    hotwords: Optional[str] = Form(default=None, description="额外热词 (空格分隔)"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON (per-request tuning)"),
):
    """全量接口：同时跑多个后端，并将结果交给 LLM 参考融合（政策/政府会议场景）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="请上传音频文件")

    metrics.increment_requests()

    if apply_llm and llm_role not in _ENSEMBLE_LLM_ROLES:
        metrics.increment_failure()
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported llm_role for /api/v1/transcribe/all: {llm_role!r}. "
                f"Must be one of: {sorted(_ENSEMBLE_LLM_ROLES)}"
            ),
        )

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        metrics.increment_failure()
        raise HTTPException(status_code=400, detail=str(e))

    t0 = time.time()
    try:
        # Convert once (ffmpeg -> 16k mono PCM) and apply preprocessing once
        # (including ClearVoice denoise if requested), then fan-out the *same*
        # WAV bytes to all backend containers. This avoids repeating heavy
        # preprocessing for each backend.
        preprocess_options = (parsed_asr_options or {}).get("preprocess")
        pcm16le_bytes: Optional[bytes] = None
        async for audio_bytes in process_audio_file(file, preprocess_options=preprocess_options):
            pcm16le_bytes = audio_bytes
            break
        if not pcm16le_bytes:
            raise HTTPException(status_code=400, detail="音频文件为空或无法解码")

        file_bytes = pcm16le_to_wav_bytes(pcm16le_bytes)

        # IMPORTANT: we've already applied preprocessing in this orchestrator.
        # Force downstream model containers to skip request-scoped + global
        # preprocessing to avoid double-normalize/double-denoise.
        forward_asr_options: dict = dict(parsed_asr_options or {})
        forward_asr_options["preprocess"] = {
            "normalize_enable": False,
            "trim_silence_enable": False,
            "denoise_enable": False,
            "vocal_separate_enable": False,
            "adaptive_enable": False,
            "remove_dc_offset": False,
            "highpass_enable": False,
            "soft_limit_enable": False,
        }

        # Ensure filename/content-type match the bytes we fan-out.
        try:
            wav_name = f"{Path(file.filename).stem or 'audio'}.wav"
        except Exception:
            wav_name = "audio.wav"

        out = await transcribe_all_models(
            file_bytes=file_bytes,
            filename=wav_name,
            content_type="audio/wav",
            with_speaker=with_speaker,
            apply_hotword=apply_hotword,
            apply_llm=apply_llm,
            llm_role=llm_role,
            hotwords=hotwords,
            asr_options=forward_asr_options,
        )

        if include_srt:
            try:
                final_obj = out.get("final") if isinstance(out, dict) else None
                if isinstance(final_obj, dict) and final_obj.get("srt") is None:
                    final_obj["srt"] = generate_srt_from_result(final_obj)
            except Exception as e:
                logger.warning("Generate SRT for ensemble final failed (ignored): %s", e)

        # Best-effort: derive audio duration from timestamps so avg_rtf is meaningful
        # even when the uploaded bytes are not PCM.
        try:
            final_obj = out.get("final") if isinstance(out, dict) else None
            duration_s = 0.0
            if isinstance(final_obj, dict):
                sentences = final_obj.get("sentences")
                if isinstance(sentences, list) and len(sentences) > 0:
                    last = sentences[-1]
                    last_end = last.get("end") if isinstance(last, dict) else None
                    if isinstance(last_end, (int, float)) and last_end > 0:
                        duration_s = float(last_end) / 1000.0

                if duration_s <= 0:
                    turns = final_obj.get("speaker_turns")
                    if isinstance(turns, list) and len(turns) > 0:
                        last = turns[-1]
                        last_end = last.get("end") if isinstance(last, dict) else None
                        if isinstance(last_end, (int, float)) and last_end > 0:
                            duration_s = float(last_end) / 1000.0

            if duration_s > 0:
                metrics.add_audio_duration(duration_s)
        except Exception:
            # Metrics must never break the response.
            pass

        metrics.add_processing_time(time.time() - t0)
        metrics.increment_success()
        return EnsembleTranscribeResponse(**out)
    except HTTPException:
        raise
    except Exception as e:
        metrics.increment_failure()
        logger.error(f"Ensemble transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"全量转写失败: {str(e)}")
