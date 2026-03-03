"""转写 API 路由"""
import asyncio
import logging
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
from src.core.ensemble import transcribe_all_models
from src.utils.service_metrics import metrics
from src.utils.subtitles import generate_srt_from_result

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["transcribe"])


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="音频文件"),
    with_speaker: bool = Form(default=False, description="是否进行说话人识别"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
    apply_llm: bool = Form(default=False, description="是否应用LLM润色"),
    llm_role: str = Form(default="default", description="LLM角色 (default/translator/code)"),
    include_srt: bool = Form(default=False, description="是否在响应中包含 SRT 字幕内容"),
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

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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
            )

            # 更新指标
            audio_duration = len(audio_bytes) / 2 / 16000  # 16bit, 16kHz
            metrics.add_audio_duration(audio_duration)
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
    llm_role: str = Form(default="default", description="LLM角色"),
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

    metrics.increment_requests()

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    results: List[BatchTranscribeItem] = []
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_single_file(index: int, file: UploadFile) -> BatchTranscribeItem:
        """处理单个文件"""
        async with semaphore:
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
                    )

                    # 更新指标
                    audio_duration = len(audio_bytes) / 2 / 16000
                    metrics.add_audio_duration(audio_duration)

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

    if success_count > 0:
        metrics.increment_success()
    if failed_count > 0:
        metrics.increment_failure()

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
    llm_role: str = Form(default="policy_meeting", description="LLM 角色（policy_meeting/meeting/corrector/...）"),
    include_srt: bool = Form(default=True, description="是否在 final 中包含 SRT 字幕内容"),
    hotwords: Optional[str] = Form(default=None, description="额外热词 (空格分隔)"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON (per-request tuning)"),
):
    """全量接口：同时跑多个后端，并将结果交给 LLM 参考融合（政策/政府会议场景）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="请上传音频文件")

    metrics.increment_requests()

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        metrics.increment_failure()
        raise HTTPException(status_code=400, detail=str(e))

    try:
        # Read once; reuse for multi-backend HTTP fan-out.
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="音频文件为空")

        out = await transcribe_all_models(
            file_bytes=file_bytes,
            filename=file.filename,
            content_type=file.content_type,
            with_speaker=with_speaker,
            apply_hotword=apply_hotword,
            apply_llm=apply_llm,
            llm_role=llm_role,
            hotwords=hotwords,
            asr_options=parsed_asr_options,
        )

        if include_srt:
            try:
                final_obj = out.get("final") if isinstance(out, dict) else None
                if isinstance(final_obj, dict) and final_obj.get("srt") is None:
                    final_obj["srt"] = generate_srt_from_result(final_obj)
            except Exception as e:
                logger.warning("Generate SRT for ensemble final failed (ignored): %s", e)

        metrics.increment_success()
        return EnsembleTranscribeResponse(**out)
    except HTTPException:
        raise
    except Exception as e:
        metrics.increment_failure()
        logger.error(f"Ensemble transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"全量转写失败: {str(e)}")
