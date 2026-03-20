"""异步转写 API - 参考 FunASR_API

支持：
- URL 音频转写（异步）
- 视频转写
- 任务结果查询
- Whisper 兼容接口
"""
import logging
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiofiles
import httpx
import ffmpeg
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api.asr_options import parse_asr_options
from src.api.dependencies import process_audio_file
from src.config import settings
from src.core.engine import transcription_engine
from src.core.task_manager import task_manager, TaskStatus
import src.core.meeting_overview as overview_mod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["async"])


def ms_to_srt_time(milliseconds: int) -> str:
    """将毫秒转换为 SRT 格式时间 (HH:MM:SS.mmm)"""
    td = timedelta(milliseconds=milliseconds)
    hours = td.seconds // 3600
    minutes = (td.seconds // 60) % 60
    seconds = td.seconds % 60
    ms = td.microseconds // 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


def convert_audio_to_pcm(input_path: str, output_path: str) -> bool:
    """
    将音频/视频转换为 16kHz 单声道 PCM WAV

    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径

    Returns:
        是否成功
    """
    try:
        (
            ffmpeg
            .input(input_path, threads=0)
            .output(output_path, format="wav", acodec="pcm_s16le", ac=1, ar=16000)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        logger.info(f"Audio converted: {input_path} -> {output_path}")
        return True
    except ffmpeg.Error as e:
        logger.error(f"FFmpeg error: {e.stderr.decode() if e.stderr else str(e)}")
        raise


def extract_audio_from_video(video_path: str, audio_path: str) -> bool:
    """
    从视频中提取音频

    Args:
        video_path: 视频文件路径
        audio_path: 输出音频路径

    Returns:
        是否成功
    """
    try:
        (
            ffmpeg
            .input(video_path)
            .output(audio_path, acodec="pcm_s16le", ac=1, ar=16000)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        logger.info(f"Audio extracted from video: {video_path}")
        return True
    except ffmpeg.Error as e:
        logger.error(f"Video extraction error: {e.stderr.decode() if e.stderr else str(e)}")
        raise


async def _save_upload_file(upload: UploadFile, dest_path: Path, *, chunk_size: int = 1024 * 1024) -> None:
    """Save an UploadFile to disk in chunks (avoid reading the whole file into memory)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest_path, "wb") as out:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            await out.write(chunk)


def _convert_path_to_pcm16le_bytes(input_path: str) -> bytes:
    """Convert any audio/video file to 16kHz mono PCM16LE bytes (s16le)."""
    try:
        audio_bytes, _ = (
            ffmpeg
            .input(str(input_path), threads=0)
            .output("-", format="s16le", acodec="pcm_s16le", ac=1, ar=16000)
            .run(cmd=["ffmpeg", "-nostdin"], capture_stdout=True, capture_stderr=True)
        )
        return audio_bytes
    except ffmpeg.Error as e:
        logger.error(f"FFmpeg error: {e.stderr.decode() if e.stderr else str(e)}")
        raise


def _handle_url_transcribe(payload: dict) -> dict:
    """
    处理 URL 转写任务

    Args:
        payload: {"url": str, "with_speaker": bool, "apply_hotword": bool}

    Returns:
        转写结果
    """
    task_id = payload.get("_task_id")
    url = payload["url"]
    with_speaker = payload.get("with_speaker", False)
    apply_hotword = payload.get("apply_hotword", True)
    apply_llm = payload.get("apply_llm", False)
    llm_role = payload.get("llm_role", "default")
    hotwords = payload.get("hotwords")
    target_backend = payload.get("target_backend")
    asr_options = payload.get("asr_options")

    # 解析 URL 获取文件扩展名
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)
    ext = os.path.splitext(filename)[1].lower() or ".wav"

    temp_download = None
    try:
        # 下载文件
        if task_id:
            task_manager.update_progress(task_id, progress=5, message="下载中")
        logger.info(f"Downloading audio from: {url}")
        with httpx.Client(timeout=60.0) as client:
            response = client.get(url, follow_redirects=True)
            response.raise_for_status()

            temp_download = tempfile.NamedTemporaryFile(
                delete=False, suffix=ext, dir=str(settings.uploads_dir)
            )
            temp_download.write(response.content)
            temp_download.close()

        # 转换格式
        if task_id:
            task_manager.update_progress(task_id, progress=10, message="转换音频")
        audio_bytes = _convert_path_to_pcm16le_bytes(temp_download.name)

        if task_id:
            # Long-audio preprocessing (including ClearVoice denoise) runs per-chunk in the engine
            # to keep timestamps stable and avoid holding a full "denoised copy" in memory.
            task_manager.update_progress(task_id, progress=15, message="准备分块")

        # 执行转写
        if task_id:
            task_manager.update_progress(task_id, progress=20, message="识别中")

        def _progress(done: int, total: int) -> None:
            if not task_id:
                return
            if total <= 0:
                return
            # Map chunk progress into 20..95 range.
            pct = 20 + int(float(done) / float(total) * 75.0)
            msg = f"分块转写 {done}/{total}"
            task_manager.update_progress(task_id, progress=pct, message=msg)

        result = transcription_engine.transcribe_long_audio(
            audio_bytes,
            with_speaker=with_speaker,
            apply_hotword=apply_hotword,
            apply_llm=apply_llm,
            llm_role=str(llm_role) if llm_role else "default",
            hotwords=hotwords,
            asr_options=asr_options,
            target_backend=target_backend,
            max_workers=1,
            progress_callback=_progress if task_id else None,
        )

        # Keep the async URL task result schema consistent with the synchronous HTTP
        # `/api/v1/transcribe` endpoint so the frontend can reuse Timeline/Exports.
        if task_id:
            task_manager.update_progress(task_id, progress=98, message="整理输出")
        out = {
            "code": 0,
            "text": result.get("text", ""),
            "text_accu": result.get("text_accu"),
            "sentences": result.get("sentences", []),
            "speaker_turns": result.get("speaker_turns"),
            "transcript": result.get("transcript"),
            "raw_text": result.get("raw_text", ""),
        }
        # Best-effort meeting overview (inline in async task result).
        if (
            bool(getattr(settings, "llm_enable", False))
            and bool(getattr(settings, "meeting_overview_enable", True))
            and bool(getattr(settings, "meeting_overview_auto", True))
        ):
            try:
                source_text = overview_mod.build_overview_source_text(result)
                if source_text:
                    out["overview"] = overview_mod.generate_meeting_overview_sync(
                        source_text,
                        role=str(getattr(settings, "meeting_overview_role", "gov_overview") or "gov_overview"),
                        max_input_chars=int(getattr(settings, "meeting_overview_max_input_chars", 12000) or 12000),
                        chunk_chars=int(getattr(settings, "meeting_overview_chunk_chars", 6000) or 6000),
                    )
            except Exception as e:
                logger.warning("Generate meeting overview failed (ignored): %s", e)
        return out

    finally:
        # 清理临时文件
        if temp_download and os.path.exists(temp_download.name):
            os.unlink(temp_download.name)


# 注册任务处理器
task_manager.register_handler("url_transcribe", _handle_url_transcribe)


def _handle_file_transcribe(payload: dict) -> dict:
    """Handle a long-audio file transcription task.

    Payload fields:
      - path: str (uploaded file path on disk)
      - filename: str (original filename, optional)
      - with_speaker/apply_hotword/apply_llm/llm_role/hotwords/asr_options/target_backend
    """
    task_id = payload.get("_task_id")
    input_path = payload.get("path")
    if not input_path:
        raise ValueError("missing payload.path")

    with_speaker = payload.get("with_speaker", False)
    apply_hotword = payload.get("apply_hotword", True)
    apply_llm = payload.get("apply_llm", False)
    llm_role = payload.get("llm_role", "default")
    hotwords = payload.get("hotwords")
    target_backend = payload.get("target_backend")
    asr_options = payload.get("asr_options")

    try:
        if task_id:
            task_manager.update_progress(task_id, progress=10, message="转换音频")
        audio_bytes = _convert_path_to_pcm16le_bytes(str(input_path))

        if task_id:
            task_manager.update_progress(task_id, progress=15, message="准备分块")

        if task_id:
            task_manager.update_progress(task_id, progress=20, message="识别中")

        def _progress(done: int, total: int) -> None:
            if not task_id:
                return
            if total <= 0:
                return
            pct = 20 + int(float(done) / float(total) * 75.0)
            msg = f"分块转写 {done}/{total}"
            task_manager.update_progress(task_id, progress=pct, message=msg)

        result = transcription_engine.transcribe_long_audio(
            audio_bytes,
            with_speaker=with_speaker,
            apply_hotword=apply_hotword,
            apply_llm=apply_llm,
            llm_role=str(llm_role) if llm_role else "default",
            hotwords=hotwords,
            asr_options=asr_options,
            target_backend=target_backend,
            max_workers=1,
            progress_callback=_progress if task_id else None,
        )

        if task_id:
            task_manager.update_progress(task_id, progress=98, message="整理输出")
        out = {
            "code": 0,
            "text": result.get("text", ""),
            "text_accu": result.get("text_accu"),
            "sentences": result.get("sentences", []),
            "speaker_turns": result.get("speaker_turns"),
            "transcript": result.get("transcript"),
            "raw_text": result.get("raw_text", ""),
        }
        if (
            bool(getattr(settings, "llm_enable", False))
            and bool(getattr(settings, "meeting_overview_enable", True))
            and bool(getattr(settings, "meeting_overview_auto", True))
        ):
            try:
                source_text = overview_mod.build_overview_source_text(result)
                if source_text:
                    out["overview"] = overview_mod.generate_meeting_overview_sync(
                        source_text,
                        role=str(getattr(settings, "meeting_overview_role", "gov_overview") or "gov_overview"),
                        max_input_chars=int(getattr(settings, "meeting_overview_max_input_chars", 12000) or 12000),
                        chunk_chars=int(getattr(settings, "meeting_overview_chunk_chars", 6000) or 6000),
                    )
            except Exception as e:
                logger.warning("Generate meeting overview failed (ignored): %s", e)
        return out
    finally:
        # Always cleanup temp files to avoid filling disk on long meetings.
        try:
            if input_path and os.path.exists(str(input_path)):
                os.unlink(str(input_path))
        except Exception:
            pass


task_manager.register_handler("file_transcribe", _handle_file_transcribe)


@router.post("/trans/url")
async def transcribe_from_url(
    audio_url: str = Form(..., description="音频/视频 URL"),
    with_speaker: bool = Form(default=False, description="是否识别说话人"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
    apply_llm: bool = Form(default=False, description="是否应用 LLM 润色"),
    llm_role: str = Form(default="default", description="LLM 角色"),
    target_backend: Optional[str] = Form(
        default=None,
        description="Router 目标后端（单端口部署专用）：auto/qwen3/vibevoice/pytorch/onnx/sensevoice/gguf/whisper...",
    ),
    hotwords: Optional[str] = Form(default=None, description="临时热词"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON (per-request tuning)"),
):
    """
    从 URL 转写音频（异步）

    提交任务后返回 task_id，通过 /result 接口查询结果。
    支持的格式：wav, mp3, m4a, flac, ogg, mp4, avi, mkv 等
    """
    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    task_id = task_manager.submit("url_transcribe", {
        "url": audio_url,
        "with_speaker": with_speaker,
        "apply_hotword": apply_hotword,
        "apply_llm": apply_llm,
        "llm_role": llm_role,
        "hotwords": hotwords,
        "target_backend": target_backend,
        "asr_options": parsed_asr_options,
    })

    return {
        "code": 200,
        "status": "success",
        "message": "任务已提交",
        "data": {"task_id": task_id}
    }


@router.post("/trans/file")
async def transcribe_from_file(
    file: UploadFile = File(..., description="音频/视频文件"),
    with_speaker: bool = Form(default=False, description="是否识别说话人"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
    apply_llm: bool = Form(default=False, description="是否应用 LLM 润色"),
    llm_role: str = Form(default="default", description="LLM 角色"),
    target_backend: Optional[str] = Form(
        default=None,
        description="Router 目标后端（单端口部署专用）：auto/qwen3/vibevoice/pytorch/onnx/sensevoice/gguf/whisper...",
    ),
    hotwords: Optional[str] = Form(default=None, description="临时热词"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON (per-request tuning)"),
):
    """上传文件转写（异步，适合 3-4 小时会议长音频）。

    提交任务后返回 task_id，通过 /result 接口轮询获取结果。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="请上传音频文件")

    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    suffix = Path(file.filename).suffix if file.filename else ".wav"
    dest = settings.uploads_dir / f"upload_{os.urandom(8).hex()}{suffix}"
    try:
        await _save_upload_file(file, dest)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存上传文件失败: {e}")

    task_id = task_manager.submit(
        "file_transcribe",
        {
            "path": str(dest),
            "filename": file.filename,
            "with_speaker": with_speaker,
            "apply_hotword": apply_hotword,
            "apply_llm": apply_llm,
            "llm_role": llm_role,
            "hotwords": hotwords,
            "target_backend": target_backend,
            "asr_options": parsed_asr_options,
        },
    )

    return {
        "code": 200,
        "status": "success",
        "message": "任务已提交",
        "data": {"task_id": task_id},
    }


@router.post("/result")
async def get_task_result(
    task_id: str = Form(..., description="任务 ID"),
    delete: bool = Form(default=True, description="获取后是否删除结果"),
):
    """
    获取异步任务结果

    - PENDING: 任务等待中
    - PROCESSING: 任务处理中
    - COMPLETED: 任务完成，返回结果
    - FAILED: 任务失败，返回错误信息
    """
    result = task_manager.get_result(task_id, delete=delete)

    if result is None:
        return {
            "code": 404,
            "status": "error",
            "message": "任务不存在或已过期"
        }

    if result.status == TaskStatus.PENDING:
        return {
            "code": 202,
            "status": "pending",
            "message": "任务等待中",
            "data": {
                "task_id": task_id,
                "progress": getattr(result, "progress", None),
                "detail": getattr(result, "message", None),
            },
        }

    if result.status == TaskStatus.PROCESSING:
        return {
            "code": 202,
            "status": "processing",
            "message": "任务处理中",
            "data": {
                "task_id": task_id,
                "progress": getattr(result, "progress", None),
                "detail": getattr(result, "message", None),
            },
        }

    if result.status == TaskStatus.FAILED:
        return {
            "code": 500,
            "status": "error",
            "message": result.error or "任务失败",
            "data": {"task_id": task_id}
        }

    # COMPLETED
    return {
        "code": 200,
        "status": "success",
        "message": "获取结果成功",
        "data": result.result
    }


@router.post("/asr")
async def asr_whisper_compatible(
    file: UploadFile = File(..., description="音频/视频文件"),
    file_type: str = Form(default="audio", description="文件类型: audio 或 video"),
    with_speaker: bool = Form(default=True, description="是否识别说话人"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
):
    """
    Whisper ASR WebService 兼容接口

    返回格式兼容 https://ahmetoner.com/whisper-asr-webservice/endpoints
    """
    temp_file = None
    temp_wav = None

    try:
        # 保存上传文件
        suffix = Path(file.filename).suffix if file.filename else ".wav"
        temp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=str(settings.uploads_dir)
        )
        content = await file.read()
        temp_file.write(content)
        temp_file.close()

        # 视频提取音频
        if file_type == "video":
            temp_wav = tempfile.NamedTemporaryFile(
                delete=False, suffix=".wav", dir=str(settings.uploads_dir)
            )
            temp_wav.close()
            extract_audio_from_video(temp_file.name, temp_wav.name)
            audio_path = temp_wav.name
        else:
            # 音频转换格式
            temp_wav = tempfile.NamedTemporaryFile(
                delete=False, suffix=".wav", dir=str(settings.uploads_dir)
            )
            temp_wav.close()
            convert_audio_to_pcm(temp_file.name, temp_wav.name)
            audio_path = temp_wav.name

        # 读取转换后的音频
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        # 执行转写
        result = await transcription_engine.transcribe_async(
            audio_bytes,
            with_speaker=with_speaker,
            apply_hotword=apply_hotword
        )

        # Whisper 兼容格式
        segments = []
        for i, sent in enumerate(result.get("sentences", []), 1):
            segments.append({
                "sentence_index": i,
                "text": sent["text"],
                "start": ms_to_srt_time(sent["start"]),
                "end": ms_to_srt_time(sent["end"]),
                "speaker": sent.get("speaker")
            })

        return {
            "text": result.get("text", ""),
            "segments": segments,
            "language": "zh"
        }

    except ffmpeg.Error as e:
        raise HTTPException(
            status_code=400,
            detail=f"音频处理失败: {e.stderr.decode() if e.stderr else str(e)}"
        )
    except Exception as e:
        logger.error(f"ASR error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"转写失败: {str(e)}")

    finally:
        if temp_file and os.path.exists(temp_file.name):
            os.unlink(temp_file.name)
        if temp_wav and os.path.exists(temp_wav.name):
            os.unlink(temp_wav.name)


@router.post("/trans/video")
async def transcribe_video(
    file: UploadFile = File(..., description="视频文件"),
    with_speaker: bool = Form(default=False, description="是否识别说话人"),
    apply_hotword: bool = Form(default=True, description="是否应用热词纠错"),
    apply_llm: bool = Form(default=False, description="是否应用 LLM 润色"),
    llm_role: str = Form(default="default", description="LLM 角色"),
    target_backend: Optional[str] = Form(
        default=None,
        description="Router 目标后端（单端口部署专用）：auto/qwen3/vibevoice/pytorch/onnx/sensevoice/gguf/whisper...",
    ),
    hotwords: Optional[str] = Form(default=None, description="临时热词"),
    asr_options: Optional[str] = Form(default=None, description="ASR options JSON (per-request tuning)"),
):
    """
    视频文件转写

    自动提取视频中的音频并转写。
    支持格式：mp4, avi, mkv, mov, webm 等
    """
    parsed_asr_options = None
    try:
        parsed_asr_options = parse_asr_options(asr_options)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        preprocess_options = (parsed_asr_options or {}).get("preprocess")
        async for audio_bytes in process_audio_file(file, preprocess_options=preprocess_options):
            # 执行转写（与 /api/v1/transcribe 保持一致：支持长音频自动 chunking）。
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

            return {
                "code": 0,
                "text": result.get("text", ""),
                "text_accu": result.get("text_accu"),
                "sentences": result.get("sentences", []),
                "speaker_turns": result.get("speaker_turns"),
                "transcript": result.get("transcript"),
                "raw_text": result.get("raw_text"),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Video transcribe error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"转写失败: {str(e)}")
