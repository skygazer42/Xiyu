"""Meeting overview generation (gov-style) via LLM.

This module is intentionally lightweight and decoupled from the ASR engine.
It is used by the API layer to generate a short official overview after
transcription. It must not import heavy ASR dependencies.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from src.config import settings
from src.core.llm.client import LLMClient, LLMMessage
from src.core.llm.roles import get_role

logger = logging.getLogger(__name__)


def build_overview_source_text(result: Dict[str, Any]) -> str:
    """Build a clean text input for meeting overview generation.

    Preference:
    - speaker_turns (speaker-labeled, no timestamps)
    - sentences (concatenated)
    - text
    """
    obj = result or {}

    turns = obj.get("speaker_turns")
    if isinstance(turns, list) and turns:
        lines: List[str] = []
        for t in turns:
            if not isinstance(t, dict):
                continue
            spk = str(t.get("speaker") or "").strip()
            txt = str(t.get("text") or "").strip()
            if not txt:
                continue
            if spk:
                lines.append(f"{spk}: {txt}")
            else:
                lines.append(txt)
        if lines:
            return "\n".join(lines).strip()

    sentences = obj.get("sentences")
    if isinstance(sentences, list) and sentences:
        parts: List[str] = []
        for s in sentences:
            if not isinstance(s, dict):
                continue
            txt = str(s.get("text") or "").strip()
            if txt:
                parts.append(txt)
        if parts:
            return "\n".join(parts).strip()

    return str(obj.get("text") or "").strip()


def chunk_text(text: str, *, chunk_chars: int) -> List[str]:
    """Split text into chunks with a soft upper bound by characters.

    We prefer splitting on line boundaries; fall back to hard slicing for
    very long lines.
    """
    t = str(text or "").strip()
    if not t:
        return []

    try:
        limit = int(chunk_chars)
    except Exception:
        limit = 0
    if limit <= 0:
        limit = 6000

    lines = [ln.strip() for ln in t.splitlines() if ln and ln.strip()]
    if not lines:
        lines = [t]

    out: List[str] = []
    buf: List[str] = []
    size = 0

    def _flush() -> None:
        nonlocal buf, size
        if not buf:
            return
        out.append("\n".join(buf).strip())
        buf = []
        size = 0

    for ln in lines:
        if not ln:
            continue

        # Hard-split single overlong lines.
        if len(ln) > limit:
            _flush()
            for i in range(0, len(ln), limit):
                seg = ln[i : i + limit].strip()
                if seg:
                    out.append(seg)
            continue

        extra = len(ln) + (1 if buf else 0)
        if buf and (size + extra) > limit:
            _flush()

        buf.append(ln)
        size += extra

    _flush()
    return [c for c in out if c and c.strip()]


def _build_messages(*, system_prompt: str, user_content: str) -> List[LLMMessage]:
    return [
        LLMMessage(role="system", content=str(system_prompt or "")),
        LLMMessage(role="user", content=str(user_content or "")),
    ]


async def _call_llm_text(llm_client: Any, messages: List[LLMMessage]) -> str:
    parts: List[str] = []
    async for chunk in llm_client.chat(messages, stream=False):
        parts.append(str(chunk))
    return "".join(parts).strip()


def _get_llm_client() -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        backend=settings.llm_backend,
        max_tokens=settings.llm_max_tokens,
        cache_enable=settings.llm_cache_enable,
        cache_size=settings.llm_cache_size,
        cache_ttl=settings.llm_cache_ttl,
    )


async def generate_meeting_overview(
    text: str,
    *,
    llm_client: Optional[Any] = None,
    role: str = "gov_overview",
    max_input_chars: Optional[int] = None,
    chunk_chars: Optional[int] = None,
) -> str:
    """Generate a gov-style meeting overview (2-5 paragraphs).

    This function is best-effort. Callers should catch exceptions and decide
    whether to fail the request.
    """
    t = str(text or "").strip()
    if not t:
        return ""

    try:
        max_chars = int(max_input_chars) if max_input_chars is not None else int(settings.meeting_overview_max_input_chars)
    except Exception:
        max_chars = 12000
    if max_chars <= 0:
        max_chars = 12000

    try:
        chunk_limit = int(chunk_chars) if chunk_chars is not None else int(settings.meeting_overview_chunk_chars)
    except Exception:
        chunk_limit = 6000
    if chunk_limit <= 0:
        chunk_limit = 6000

    role_obj = get_role(role)
    system_prompt = role_obj.system_prompt

    client = llm_client or _get_llm_client()

    # Single-pass for medium inputs.
    if len(t) <= max_chars:
        messages = _build_messages(
            system_prompt=system_prompt,
            user_content=f"会议转写文本如下：\n\n{t}\n\n请按要求输出会议概览。",
        )
        return await _call_llm_text(client, messages)

    # Two-phase for long inputs: extract factual notes, then synthesize overview.
    chunks = chunk_text(t, chunk_chars=chunk_limit)
    if not chunks:
        chunks = [t[:max_chars]]

    notes: List[str] = []
    for i, ch in enumerate(chunks, 1):
        user = (
            "请从以下会议转写片段中提取事实性要点（不要写概览，不要修辞，不要扩写）。\n"
            "要求：\n"
            "- 仅输出要点文本（可用换行分隔短句），不要编号，不要标题。\n"
            "- 不得编造事实。\n\n"
            f"片段 {i}/{len(chunks)}：\n{ch}"
        )
        messages = _build_messages(system_prompt=system_prompt, user_content=user)
        note = await _call_llm_text(client, messages)
        if note:
            notes.append(note)

    merged_notes = "\n".join([n.strip() for n in notes if n and n.strip()]).strip()
    if not merged_notes:
        merged_notes = t[:max_chars]

    final_user = (
        "以下为会议转写的事实要点。请基于这些要点，生成 2 到 5 段政务口径会议概览。\n"
        "要求：措辞正式、第三人称、不得编造事实、不要标题/编号/项目符号。\n\n"
        f"事实要点：\n{merged_notes}"
    )
    final_messages = _build_messages(system_prompt=system_prompt, user_content=final_user)
    return await _call_llm_text(client, final_messages)


def generate_meeting_overview_sync(*args: Any, **kwargs: Any) -> str:
    """Sync wrapper for environments where only sync call sites are convenient."""
    coro = generate_meeting_overview(*args, **kwargs)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Running inside an event loop (unlikely for TaskManager worker threads).
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return asyncio.run(coro)


def _handle_meeting_overview_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    """TaskManager handler: generate overview text from payload."""
    p = payload or {}
    text = str(p.get("text") or "").strip()
    role = str(p.get("role") or settings.meeting_overview_role or "gov_overview").strip() or "gov_overview"

    max_chars = p.get("max_input_chars", None)
    chunk_chars = p.get("chunk_chars", None)

    if not text:
        return {"overview": ""}

    overview = generate_meeting_overview_sync(
        text,
        role=role,
        max_input_chars=max_chars,
        chunk_chars=chunk_chars,
    )
    return {"overview": overview}


# Register task handler (best-effort). This keeps the API layer simple: it can
# submit `meeting_overview` tasks without worrying about import order.
try:
    from src.core.task_manager import task_manager

    task_manager.register_handler("meeting_overview", _handle_meeting_overview_task)
except Exception as e:
    logger.debug("meeting_overview task handler not registered: %s", e)
