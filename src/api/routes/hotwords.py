"""Hotwords / rules / rectify management APIs.

This module exposes HTTP endpoints for:
- forced hotwords: strong replacement/correction
- context hotwords: prompt injection only (no forced replacement)
- hot-rules.txt: regex/equals rule replacement
- hot-rectify.txt: correction history for LLM prompt retrieval (RAG)
"""

import logging
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import settings
from src.core.engine import transcription_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hotwords", tags=["hotwords"])


class HotwordsListResponse(BaseModel):
    code: int = 0
    hotwords: List[str] = Field(..., description="热词列表")
    count: int = Field(..., description="热词数量")


class HotwordsUpdateRequest(BaseModel):
    hotwords: List[str] = Field(..., description="热词列表")


class HotwordsUpdateResponse(BaseModel):
    code: int = 0
    count: int = Field(..., description="更新后的热词数量")
    message: str = "success"


class TextFileResponse(BaseModel):
    code: int = 0
    text: str = Field("", description="文件内容（UTF-8）")
    count: int = Field(0, description="解析后的条目数量")
    message: str = "success"


class TextFileUpdateRequest(BaseModel):
    text: str = Field(..., description="文件内容（UTF-8）")


class RectifyAppendRequest(BaseModel):
    wrong: str = Field(..., description="错误文本")
    right: str = Field(..., description="正确文本")


def _ensure_hotwords_dir() -> Path:
    d = Path(settings.hotwords_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_text_file(path: Path) -> str:
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {path.name}: {e}")


def _write_text_file(path: Path, text: str) -> None:
    try:
        _ensure_hotwords_dir()
        path.write_text(text or "", encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {path.name}: {e}")


def _count_rules(text: str) -> int:
    cnt = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(" = ", 1)
        if len(parts) == 2 and parts[0].strip():
            cnt += 1
    return cnt


def _count_rectify_records(text: str) -> int:
    content = text or ""
    if not content.strip():
        return 0
    cnt = 0
    for block in content.split("---"):
        lines = [
            line.strip()
            for line in block.strip().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if len(lines) >= 2 and lines[0] and lines[1]:
            cnt += 1
    return cnt


@router.get("", response_model=HotwordsListResponse)
async def get_hotwords():
    """获取当前热词列表"""
    hotwords = list(transcription_engine.corrector.hotwords.keys())
    return HotwordsListResponse(
        hotwords=hotwords,
        count=len(hotwords)
    )


@router.get("/context", response_model=HotwordsListResponse)
async def get_context_hotwords():
    """获取当前上下文热词列表（仅用于注入提示，不做强制替换）"""
    hotwords = list(getattr(transcription_engine, "_context_hotwords_list", []) or [])
    # Defensive: keep only non-empty strings.
    hotwords = [str(h).strip() for h in hotwords if str(h).strip()]
    return HotwordsListResponse(
        hotwords=hotwords,
        count=len(hotwords),
    )


@router.post("", response_model=HotwordsUpdateResponse)
async def update_hotwords(request: HotwordsUpdateRequest):
    """更新热词列表 (替换全部)"""
    try:
        transcription_engine.update_hotwords(request.hotwords)
        return HotwordsUpdateResponse(
            count=len(request.hotwords),
            message="热词更新成功"
        )
    except Exception as e:
        logger.error(f"Failed to update hotwords: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context", response_model=HotwordsUpdateResponse)
async def update_context_hotwords(request: HotwordsUpdateRequest):
    """更新上下文热词列表 (替换全部)"""
    try:
        transcription_engine.update_context_hotwords(request.hotwords)
        return HotwordsUpdateResponse(
            count=len(request.hotwords),
            message="上下文热词更新成功",
        )
    except Exception as e:
        logger.error(f"Failed to update context hotwords: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/append", response_model=HotwordsUpdateResponse)
async def append_hotwords(request: HotwordsUpdateRequest):
    """追加热词 (保留现有)"""
    try:
        existing = list(transcription_engine.corrector.hotwords.keys())
        combined = list(set(existing + request.hotwords))
        transcription_engine.update_hotwords(combined)
        return HotwordsUpdateResponse(
            count=len(combined),
            message=f"追加了 {len(request.hotwords)} 个热词"
        )
    except Exception as e:
        logger.error(f"Failed to append hotwords: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context/append", response_model=HotwordsUpdateResponse)
async def append_context_hotwords(request: HotwordsUpdateRequest):
    """追加上下文热词 (保留现有)"""
    try:
        existing = list(getattr(transcription_engine, "_context_hotwords_list", []) or [])
        existing_norm = [str(h).strip() for h in existing if str(h).strip()]
        existing_set = set(existing_norm)

        to_append = []
        for h in request.hotwords:
            s = str(h).strip()
            if not s:
                continue
            if s in existing_set:
                continue
            existing_set.add(s)
            to_append.append(s)

        combined = existing_norm + to_append
        transcription_engine.update_context_hotwords(combined)
        return HotwordsUpdateResponse(
            count=len(combined),
            message=f"追加了 {len(to_append)} 个上下文热词",
        )
    except Exception as e:
        logger.error(f"Failed to append context hotwords: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reload", response_model=HotwordsUpdateResponse)
async def reload_hotwords():
    """从文件重新加载热词"""
    try:
        transcription_engine.load_hotwords()
        count = len(transcription_engine.corrector.hotwords)
        return HotwordsUpdateResponse(
            count=count,
            message="热词重新加载成功"
        )
    except Exception as e:
        logger.error(f"Failed to reload hotwords: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context/reload", response_model=HotwordsUpdateResponse)
async def reload_context_hotwords():
    """从文件重新加载上下文热词"""
    try:
        transcription_engine.load_context_hotwords()
        count = len(getattr(transcription_engine, "_context_hotwords_list", []) or [])
        return HotwordsUpdateResponse(
            count=count,
            message="上下文热词重新加载成功",
        )
    except Exception as e:
        logger.error(f"Failed to reload context hotwords: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------
# Rules (hot-rules.txt)
# ------------------------------------------------------------


@router.get("/rules", response_model=TextFileResponse)
async def get_rules_text() -> TextFileResponse:
    """获取 hot-rules.txt 内容（正则/等号规则）。"""
    path = _ensure_hotwords_dir() / "hot-rules.txt"
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rules(text))


@router.post("/rules", response_model=TextFileResponse)
async def update_rules_text(request: TextFileUpdateRequest) -> TextFileResponse:
    """更新 hot-rules.txt（覆盖写入）并立即 reload。"""
    path = _ensure_hotwords_dir() / "hot-rules.txt"
    _write_text_file(path, request.text)
    try:
        transcription_engine.load_rules(str(path))
    except Exception as e:
        logger.error(f"Failed to reload rules after update: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rules(text), message="规则更新成功")


@router.post("/rules/append", response_model=TextFileResponse)
async def append_rules_text(request: TextFileUpdateRequest) -> TextFileResponse:
    """追加写入 hot-rules.txt 并立即 reload。"""
    path = _ensure_hotwords_dir() / "hot-rules.txt"
    existing = _read_text_file(path)
    add = request.text or ""
    if add.strip():
        if existing and not existing.endswith("\n"):
            existing += "\n"
        existing += add
    _write_text_file(path, existing)
    try:
        transcription_engine.load_rules(str(path))
    except Exception as e:
        logger.error(f"Failed to reload rules after append: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rules(text), message="规则追加成功")


@router.post("/rules/reload", response_model=TextFileResponse)
async def reload_rules() -> TextFileResponse:
    """从文件重载 hot-rules.txt。"""
    path = _ensure_hotwords_dir() / "hot-rules.txt"
    try:
        transcription_engine.load_rules(str(path))
    except Exception as e:
        logger.error(f"Failed to reload rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rules(text), message="规则重新加载成功")


# ------------------------------------------------------------
# Rectify history (hot-rectify.txt)
# ------------------------------------------------------------


@router.get("/rectify", response_model=TextFileResponse)
async def get_rectify_text() -> TextFileResponse:
    """获取 hot-rectify.txt 内容（纠错历史）。"""
    path = _ensure_hotwords_dir() / "hot-rectify.txt"
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rectify_records(text))


@router.post("/rectify", response_model=TextFileResponse)
async def update_rectify_text(request: TextFileUpdateRequest) -> TextFileResponse:
    """更新 hot-rectify.txt（覆盖写入）并立即 reload。"""
    path = _ensure_hotwords_dir() / "hot-rectify.txt"
    _write_text_file(path, request.text)
    try:
        transcription_engine.load_rectify_history(str(path))
    except Exception as e:
        logger.error(f"Failed to reload rectify history after update: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rectify_records(text), message="纠错历史更新成功")


@router.post("/rectify/append", response_model=TextFileResponse)
async def append_rectify_record(request: RectifyAppendRequest) -> TextFileResponse:
    """追加一条纠错记录到 hot-rectify.txt 并立即 reload。"""
    wrong = str(request.wrong or "").strip()
    right = str(request.right or "").strip()
    if not wrong or not right:
        raise HTTPException(status_code=400, detail="wrong/right must be non-empty")

    path = _ensure_hotwords_dir() / "hot-rectify.txt"
    existing = _read_text_file(path)

    block = f"{wrong}\n{right}\n"
    if existing.strip():
        new_text = existing.rstrip() + "\n\n---\n" + block
    else:
        new_text = block

    _write_text_file(path, new_text)
    try:
        transcription_engine.load_rectify_history(str(path))
    except Exception as e:
        logger.error(f"Failed to reload rectify history after append: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rectify_records(text), message="纠错记录追加成功")


@router.post("/rectify/reload", response_model=TextFileResponse)
async def reload_rectify_history() -> TextFileResponse:
    """从文件重载 hot-rectify.txt。"""
    path = _ensure_hotwords_dir() / "hot-rectify.txt"
    try:
        transcription_engine.load_rectify_history(str(path))
    except Exception as e:
        logger.error(f"Failed to reload rectify history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    text = _read_text_file(path)
    return TextFileResponse(text=text, count=_count_rectify_records(text), message="纠错历史重新加载成功")
