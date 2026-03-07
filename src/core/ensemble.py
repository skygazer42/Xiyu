"""Multi-backend (all-model) transcription + LLM fusion.

This module is intended to run inside a Docker Compose multi-model network:
- It calls other Xiyu containers via HTTP (service names).
- It selects one "base" backend to produce speaker turns (with_speaker=true).
- It runs all other backends for reference (with_speaker=false).
- It asks an LLM to polish the base speaker turns using multi-ASR references.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.config import settings
from src.core.engine import transcription_engine
from src.core.llm import LLMMessage
from src.core.llm.roles import get_role
from src.core.speaker import SpeakerLabeler
from src.core.text_processor.post_processor import PostProcessorSettings, TextPostProcessor

logger = logging.getLogger(__name__)


DEFAULT_DOCKER_TARGETS: Dict[str, str] = {
    "pytorch": "http://xiyu-pytorch:8000",
    "onnx": "http://xiyu-onnx:8000",
    "sensevoice": "http://xiyu-sensevoice:8000",
    "gguf": "http://xiyu-gguf:8000",
    "whisper": "http://xiyu-whisper:8000",
    "qwen3": "http://xiyu-qwen3:8000",
    "vibevoice": "http://xiyu-vibevoice:8000",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _strip_code_fences(s: str) -> str:
    # Common LLM behavior: wrap JSON in ```json ... ```
    if not s:
        return s
    t = s.strip()
    if t.startswith("```"):
        # remove leading ```lang and trailing ```
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_json_blob(s: str) -> Optional[str]:
    """Best-effort JSON extraction from an LLM response."""
    if not s:
        return None
    t = _strip_code_fences(s)
    # Prefer object, then array.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        i = t.find(open_ch)
        j = t.rfind(close_ch)
        if i >= 0 and j > i:
            return t[i : j + 1]
    return None


def _escape_control_chars_in_json_strings(s: str) -> str:
    """Escape raw control characters inside JSON string literals.

    Some LLMs output JSON-like objects but include literal newlines inside string
    values (invalid JSON). This helper keeps the JSON structure intact while
    making it parseable by `json.loads`.
    """
    if not s:
        return s

    out: List[str] = []
    in_str = False
    escape = False

    for ch in s:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == "\"":
                out.append(ch)
                in_str = False
                continue

            # Escape control chars that would break JSON parsing inside strings.
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            if ord(ch) < 0x20:
                # Replace other control chars with a space (lossy but safe).
                out.append(" ")
                continue

            out.append(ch)
            continue

        # Outside strings
        if ch == "\"":
            out.append(ch)
            in_str = True
            continue
        out.append(ch)

    return "".join(out)


def _try_parse_json(blob: str) -> Tuple[Optional[object], Optional[Exception]]:
    """Parse JSON with a few best-effort repairs.

    This is intentionally conservative: we only apply transformations that are
    very likely to be correct for LLM-emitted JSON (control-char escaping and
    trailing-comma removal).
    """
    if not blob:
        return None, ValueError("empty json blob")

    last_err: Optional[Exception] = None
    candidates: List[str] = []

    s0 = _escape_control_chars_in_json_strings(blob)
    candidates.append(s0)

    # Common LLM mistake: trailing commas before '}' / ']'.
    s1 = re.sub(r",\s*([}\]])", r"\1", s0)
    if s1 != s0:
        candidates.append(s1)

    for s in candidates:
        try:
            return json.loads(s), None
        except Exception as e:
            last_err = e

    return None, last_err


async def _llm_fix_json_blob(blob: str) -> Optional[str]:
    """Ask the LLM to reformat an invalid JSON blob into strict JSON."""
    if not blob:
        return None

    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are a strict JSON formatter. "
                "Given an invalid JSON-like string, output valid JSON ONLY. "
                "No explanations, no markdown/code fences."
            ),
        ),
        LLMMessage(role="user", content=f"Fix this into valid JSON:\n\n{blob}\n\nOutput ONLY JSON."),
    ]

    parts: List[str] = []
    async for chunk in transcription_engine.llm_client.chat(messages, stream=False):
        parts.append(chunk)
    raw = "".join(parts).strip()
    return _extract_json_blob(raw) or raw


def _clean_text_for_llm(obj: Dict[str, Any]) -> str:
    """Normalize Xiyu response to a clean reference text for LLM."""
    # Prefer structured fields to avoid backend quirks (e.g., VibeVoice embedding JSON in `text`).
    speaker_turns = obj.get("speaker_turns")
    if isinstance(speaker_turns, list) and speaker_turns:
        parts: List[str] = []
        for t in speaker_turns:
            if not isinstance(t, dict):
                continue
            spk = str(t.get("speaker") or "").strip()
            txt = str(t.get("text") or "").strip()
            if not txt:
                continue
            if spk:
                parts.append(f"{spk}: {txt}")
            else:
                parts.append(txt)
        if parts:
            return "\n".join(parts)

    sentences = obj.get("sentences")
    if isinstance(sentences, list) and sentences:
        parts = []
        for s in sentences:
            if not isinstance(s, dict):
                continue
            txt = str(s.get("text") or "").strip()
            if txt:
                parts.append(txt)
        if parts:
            return "\n".join(parts)

    text = str(obj.get("text") or "").strip()
    if not text:
        return ""

    # Qwen3 wrapper sometimes returns "language Chinese<asr_text>" markers.
    text = text.replace("language Chinese<asr_text>", "")

    # If it looks like it appended a JSON array after the plain text, strip the tail.
    # We keep it conservative: only strip if a JSON array starts after some non-empty text.
    m = re.search(r"\n\s*\[\s*\{", text)
    if m and m.start() > 0:
        text = text[: m.start()].strip()

    return text


def _merge_asr_options(
    base: Optional[Dict[str, Any]],
    *,
    with_speaker: bool,
    default_label_style: str = "numeric",
    max_workers: int = 1,
) -> Optional[Dict[str, Any]]:
    if base is None:
        base = {}
    if not isinstance(base, dict):
        return None

    out: Dict[str, Any] = dict(base)

    # Stability-first defaults for long audio: avoid multi-thread chunk transcribe issues.
    chunking = out.get("chunking")
    if not isinstance(chunking, dict):
        chunking = {}
    if "max_workers" not in chunking:
        chunking["max_workers"] = int(max_workers)
    out["chunking"] = chunking

    if with_speaker:
        speaker = out.get("speaker")
        if not isinstance(speaker, dict):
            speaker = {}
        speaker.setdefault("label_style", str(default_label_style))
        out["speaker"] = speaker

    return out


def _parse_targets(spec: str) -> Dict[str, str]:
    """Parse `name=url` pairs from a string (comma/newline separated)."""
    out: Dict[str, str] = {}
    if not spec:
        return out

    parts = re.split(r"[,\n]+", str(spec))
    for p in parts:
        s = str(p).strip()
        if not s:
            continue
        if "=" not in s:
            raise ValueError("ENSEMBLE_TARGETS must be 'name=url' pairs separated by comma/newline")
        name, url = s.split("=", 1)
        name = name.strip()
        url = url.strip().rstrip("/")
        if not name or not url:
            continue
        out[name] = url
    return out


def get_ensemble_targets() -> Dict[str, str]:
    # Prefer explicit env/config; fall back to docker-compose service names.
    spec = str(getattr(settings, "ensemble_targets", "") or "").strip()
    if spec:
        return _parse_targets(spec)
    return dict(DEFAULT_DOCKER_TARGETS)


@dataclass
class CandidateResult:
    backend: str
    base_url: str
    success: bool
    http_status: Optional[int]
    code: Optional[int]
    elapsed_ms: int
    result_obj: Optional[Dict[str, Any]] = None
    cleaned_text: str = ""
    error: Optional[str] = None


async def _call_xiyu_transcribe(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str],
    with_speaker: bool,
    apply_hotword: bool,
    hotwords: Optional[str],
    asr_options: Optional[Dict[str, Any]],
    timeout_s: float,
) -> Tuple[Optional[int], Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/v1/transcribe"

    data: Dict[str, str] = {
        "with_speaker": "true" if with_speaker else "false",
        "apply_hotword": "true" if apply_hotword else "false",
        # The ensemble endpoint applies LLM once at the end.
        "apply_llm": "false",
        "llm_role": "default",
    }
    if hotwords:
        data["hotwords"] = str(hotwords)
    if asr_options:
        data["asr_options"] = json.dumps(asr_options, ensure_ascii=False)

    files = {
        "file": (filename, file_bytes, content_type or "application/octet-stream"),
    }

    try:
        resp = await client.post(url, data=data, files=files, timeout=timeout_s)
    except Exception as e:
        raise RuntimeError(f"POST {url} failed: {e}") from e

    http_status = int(resp.status_code)
    try:
        obj = resp.json()
    except Exception as e:
        body = (resp.text or "")[:2000]
        raise RuntimeError(f"Invalid JSON from {url} (HTTP {http_status}): {e}; body_head={body!r}") from e

    if not isinstance(obj, dict):
        raise RuntimeError(f"Invalid response shape from {url}: expected object, got {type(obj)}")

    return http_status, obj


def _pick_base_result(
    candidates: List[CandidateResult],
    *,
    preferred_backend: str,
    require_speaker_turns: bool,
) -> Optional[CandidateResult]:
    # Prefer the configured base backend.
    for c in candidates:
        if c.backend == preferred_backend and c.success and c.result_obj:
            if require_speaker_turns:
                turns = c.result_obj.get("speaker_turns")
                if isinstance(turns, list) and turns:
                    return c
            else:
                return c

    # Otherwise, pick any successful candidate.
    for c in candidates:
        if not c.success or not c.result_obj:
            continue
        if require_speaker_turns:
            turns = c.result_obj.get("speaker_turns")
            if isinstance(turns, list) and turns:
                return c
        else:
            return c
    return None


async def _llm_polish_turn_texts(
    *,
    base_turns: List[Dict[str, Any]],
    references: Dict[str, str],
    role_name: str,
    max_reference_chars: int = 12000,
) -> Optional[Dict[int, str]]:
    if not base_turns:
        return None

    role = get_role(role_name)
    system_prompt = role.system_prompt

    # Hotwords hint for policy meetings.
    # Keep it bounded to avoid bloating prompts.
    hotwords: List[str] = []
    try:
        transcription_engine.load_all()
        if getattr(transcription_engine, "_context_hotwords_list", None):
            hotwords.extend(list(transcription_engine._context_hotwords_list)[:80])  # type: ignore[attr-defined]
        if getattr(transcription_engine, "_hotwords_list", None):
            for hw in list(transcription_engine._hotwords_list)[:80]:  # type: ignore[attr-defined]
                if hw not in hotwords:
                    hotwords.append(hw)
    except Exception:
        pass

    # Render base turns as JSON for deterministic mapping.
    base_for_prompt: List[Dict[str, Any]] = []
    for idx, t in enumerate(base_turns):
        if not isinstance(t, dict):
            continue
        base_for_prompt.append(
            {
                "idx": idx,
                "speaker": t.get("speaker"),
                "speaker_id": t.get("speaker_id"),
                "start": t.get("start"),
                "end": t.get("end"),
                "text": t.get("text"),
            }
        )

    ref_sections: List[str] = []
    total_ref = 0
    for name, text in references.items():
        if not text:
            continue
        remaining = max_reference_chars - total_ref
        if remaining <= 0:
            break
        chunk = text[:remaining]
        total_ref += len(chunk)
        ref_sections.append(f"## {name}\n{chunk}\n")

    user_content = "\n".join(
        [
            "# 多模型参考转写（同一音频）",
            *(ref_sections or ["(无参考文本)"]),
            "",
            "# 热词/术语提示（优先采用这些写法）",
            " ".join(hotwords[:120]) if hotwords else "(无)",
            "",
            "# Base turns（请仅修改 text，保留 idx 对应关系）",
            json.dumps(base_for_prompt, ensure_ascii=False),
            "",
            "# 输出要求",
            "仅输出严格 JSON：{\"turns\":[{\"idx\":0,\"text\":\"...\"}, ...]}",
        ]
    )

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_content),
    ]

    # Call LLM (single-shot, non-streaming).
    parts: List[str] = []
    async for chunk in transcription_engine.llm_client.chat(messages, stream=False):
        parts.append(chunk)
    raw = "".join(parts).strip()

    blob = _extract_json_blob(raw)
    if not blob:
        logger.warning("LLM returned non-JSON output; skipping polish")
        return None

    obj, err = _try_parse_json(blob)
    if obj is None:
        logger.warning("Failed to parse LLM JSON output: %s", err)
        # One retry: ask the LLM to fix its own JSON.
        try:
            fixed = await _llm_fix_json_blob(blob)
            if fixed:
                obj2, err2 = _try_parse_json(fixed)
                if obj2 is not None:
                    obj = obj2
                else:
                    logger.warning("Failed to parse fixed LLM JSON output: %s", err2)
                    return None
            else:
                return None
        except Exception as e:
            logger.warning("LLM JSON fix retry failed: %s", e)
            return None

    turns_obj: Any = None
    if isinstance(obj, dict):
        # Our prompt requests {"turns":[...]} but be tolerant.
        turns_obj = obj.get("turns") if "turns" in obj else obj.get("data") or obj.get("result")
    elif isinstance(obj, list):
        turns_obj = obj

    if turns_obj is None:
        return None

    mapping: Dict[int, str] = {}

    # Case A: {"turns": {"0": "...", "1": "..."}}
    if isinstance(turns_obj, dict):
        for k, v in turns_obj.items():
            try:
                idx_i = int(k)
            except Exception:
                continue
            if idx_i < 0 or idx_i >= len(base_turns):
                continue
            s = str(v or "").strip()
            if s:
                mapping[idx_i] = s
        return mapping or None

    # Case B: {"turns":[{"idx":0,"text":"..."}, ...]} or ["...", "..."]
    if not isinstance(turns_obj, list) or not turns_obj:
        return None

    for pos, item in enumerate(turns_obj):
        idx = None
        txt = None
        if isinstance(item, dict):
            idx = (
                item.get("idx")
                if "idx" in item
                else item.get("index")
                if "index" in item
                else item.get("i")
                if "i" in item
                else item.get("id")
            )
            txt = (
                item.get("text")
                if "text" in item
                else item.get("content")
                if "content" in item
                else item.get("value")
            )
        elif isinstance(item, str):
            idx = pos
            txt = item

        try:
            idx_i = int(idx)
        except Exception:
            continue
        if idx_i < 0 or idx_i >= len(base_turns):
            continue

        s = str(txt or "").strip()
        if not s:
            continue
        mapping[idx_i] = s

    return mapping or None


async def transcribe_all_models(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str],
    with_speaker: bool = True,
    apply_hotword: bool = True,
    apply_llm: bool = True,
    llm_role: Optional[str] = None,
    hotwords: Optional[str] = None,
    asr_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run multi-model transcription and optionally fuse with LLM.

    Returns a dict suitable for `EnsembleTranscribeResponse` serialization.
    """
    targets = get_ensemble_targets()

    base_backend = str(getattr(settings, "ensemble_base_backend", "") or "").strip() or "pytorch"
    llm_role_effective = (
        str(llm_role).strip()
        if llm_role is not None and str(llm_role).strip()
        else str(getattr(settings, "ensemble_llm_role", "") or "").strip() or "policy_meeting"
    )
    timeout_s = float(getattr(settings, "ensemble_timeout_s", 600.0) or 600.0)
    max_concurrent = int(getattr(settings, "ensemble_max_concurrent", 4) or 4)
    if max_concurrent <= 0:
        max_concurrent = 1

    merged_asr_options = _merge_asr_options(asr_options, with_speaker=with_speaker)
    post_processor = TextPostProcessor.from_config(settings)
    try:
        postprocess_section = (
            (merged_asr_options or {}).get("postprocess") if isinstance(merged_asr_options, dict) else None
        )
        if isinstance(postprocess_section, dict) and postprocess_section:
            pp_settings = PostProcessorSettings(
                filler_remove_enable=settings.filler_remove_enable,
                filler_aggressive=settings.filler_aggressive,
                qj2bj_enable=settings.qj2bj_enable,
                itn_enable=settings.itn_enable,
                itn_erhua_remove=settings.itn_erhua_remove,
                spacing_cjk_ascii_enable=settings.spacing_cjk_ascii_enable,
                spoken_punc_enable=settings.spoken_punc_enable,
                acronym_merge_enable=settings.acronym_merge_enable,
                gov_format_enable=getattr(settings, "gov_format_enable", True),
                zh_convert_enable=settings.zh_convert_enable,
                zh_convert_locale=settings.zh_convert_locale,
                punc_convert_enable=settings.punc_convert_enable,
                punc_add_space=settings.punc_add_space,
                punc_restore_enable=settings.punc_restore_enable,
                punc_restore_model=settings.punc_restore_model,
                punc_restore_device=settings.device,
                punc_merge_enable=settings.punc_merge_enable,
                trash_punc_enable=settings.trash_punc_enable,
                trash_punc_chars=settings.trash_punc_chars,
            )
            for k, v in postprocess_section.items():
                if hasattr(pp_settings, k):
                    setattr(pp_settings, k, v)
            post_processor = TextPostProcessor(pp_settings)
    except Exception:
        post_processor = TextPostProcessor.from_config(settings)

    # One shared HTTP client for connection pooling.
    limits = httpx.Limits(max_connections=max(8, len(targets) + 2), max_keepalive_connections=8)
    async with httpx.AsyncClient(limits=limits) as client:
        sem = asyncio.Semaphore(max_concurrent)

        async def _run_one(name: str, base_url: str) -> CandidateResult:
            t0 = _now_ms()
            want_speaker = bool(with_speaker) and (name == base_backend)
            try:
                async with sem:
                    http_status, obj = await _call_xiyu_transcribe(
                        client,
                        base_url=base_url,
                        file_bytes=file_bytes,
                        filename=filename,
                        content_type=content_type,
                        with_speaker=want_speaker,
                        apply_hotword=apply_hotword,
                        hotwords=hotwords,
                        asr_options=merged_asr_options,
                        timeout_s=timeout_s,
                    )
                code = obj.get("code") if isinstance(obj, dict) else None
                cleaned = _clean_text_for_llm(obj if isinstance(obj, dict) else {})
                ok = bool(isinstance(code, int) and code == 0)
                return CandidateResult(
                    backend=name,
                    base_url=base_url,
                    success=ok,
                    http_status=http_status,
                    code=int(code) if isinstance(code, int) else None,
                    elapsed_ms=_now_ms() - t0,
                    result_obj=obj if isinstance(obj, dict) else None,
                    cleaned_text=cleaned,
                    error=None if ok else f"Xiyu code={code!r}",
                )
            except Exception as e:
                return CandidateResult(
                    backend=name,
                    base_url=base_url,
                    success=False,
                    http_status=None,
                    code=None,
                    elapsed_ms=_now_ms() - t0,
                    result_obj=None,
                    cleaned_text="",
                    error=str(e),
                )

        tasks = [_run_one(name, url) for name, url in targets.items()]
        candidates = await asyncio.gather(*tasks)

    base = _pick_base_result(
        candidates,
        preferred_backend=base_backend,
        require_speaker_turns=bool(with_speaker),
    )
    if base is None or not base.result_obj:
        raise RuntimeError("no successful candidate result available")

    base_obj = base.result_obj
    base_turns = base_obj.get("speaker_turns") if isinstance(base_obj, dict) else None

    # If we asked for speaker turns but didn't get them, return base as-is (still useful).
    if with_speaker and (not isinstance(base_turns, list) or not base_turns):
        apply_llm = False

    llm_used = False
    polished_map: Optional[Dict[int, str]] = None
    if apply_llm and bool(settings.llm_enable):
        try:
            refs: Dict[str, str] = {}
            for c in candidates:
                if not c.cleaned_text:
                    continue
                refs[c.backend] = c.cleaned_text

            polished_map = await _llm_polish_turn_texts(
                base_turns=list(base_turns) if isinstance(base_turns, list) else [],
                references=refs,
                role_name=llm_role_effective,
            )
            llm_used = polished_map is not None
        except Exception as e:
            logger.warning("LLM polish failed (ignored): %s", e)
            polished_map = None
            llm_used = False

    # Build final result (TranscribeResponse-compatible dict).
    final_turns: Optional[List[Dict[str, Any]]] = None
    if isinstance(base_turns, list) and base_turns:
        final_turns = []
        for idx, t in enumerate(base_turns):
            if not isinstance(t, dict):
                continue
            t2 = dict(t)
            if polished_map and idx in polished_map:
                t2["text"] = polished_map[idx]
            final_turns.append(t2)

        # Final numeric/template formatting after LLM (best-effort).
        if apply_hotword:
            try:
                for t in final_turns:
                    if t.get("text"):
                        t["text"] = post_processor.process_final(str(t.get("text") or ""))
            except Exception as e:
                logger.warning("Postprocess final for ensemble turns failed (ignored): %s", e)

        # Recompute transcript + sentences from (possibly updated) turns.
        label_style = "numeric"
        try:
            speaker_section = (merged_asr_options or {}).get("speaker") if isinstance(merged_asr_options, dict) else None
            if isinstance(speaker_section, dict) and speaker_section.get("label_style"):
                label_style = str(speaker_section.get("label_style") or "numeric")
        except Exception:
            label_style = "numeric"
        speaker_labeler = SpeakerLabeler(label_style=label_style)
        transcript = speaker_labeler.format_transcript(final_turns, include_timestamp=True)

        # Use turns as timeline sentences too (consistent with external diarizer path).
        sentences = [
            {
                "text": str(t.get("text") or ""),
                "start": int(t.get("start") or 0),
                "end": int(t.get("end") or 0),
                "speaker": t.get("speaker"),
                "speaker_id": t.get("speaker_id"),
            }
            for t in final_turns
        ]

        raw_text = ""
        try:
            raw_text = "\n".join(str(t.get("text") or "") for t in base_turns if isinstance(t, dict)).strip()
        except Exception:
            raw_text = ""

        text = "\n".join(str(t.get("text") or "") for t in final_turns).strip()

        final_obj: Dict[str, Any] = {
            "code": 0,
            "text": text,
            "text_accu": None,
            "sentences": sentences,
            "speaker_turns": final_turns,
            "transcript": transcript,
            "raw_text": raw_text if raw_text and raw_text != text else None,
        }
    else:
        # No turns available: just return base result as "final".
        final_obj = dict(base_obj)
        final_obj["code"] = 0

    # Candidate payload for API response (bounded).
    candidate_payload = []
    for c in candidates:
        text = None
        cleaned = None
        if c.result_obj and isinstance(c.result_obj, dict):
            try:
                text = str(c.result_obj.get("text") or "")
            except Exception:
                text = None
        if c.cleaned_text:
            cleaned = c.cleaned_text

        # Hard cap to avoid huge responses (vibevoice can include JSON blobs).
        if isinstance(text, str) and len(text) > 4000:
            text = text[:4000] + "..."
        if isinstance(cleaned, str) and len(cleaned) > 4000:
            cleaned = cleaned[:4000] + "..."

        candidate_payload.append(
            {
                "backend": c.backend,
                "base_url": c.base_url,
                "success": bool(c.success),
                "http_status": c.http_status,
                "code": c.code,
                "elapsed_ms": int(c.elapsed_ms),
                "text": text,
                "cleaned_text": cleaned,
                "error": c.error,
            }
        )

    return {
        "code": 0,
        "base_backend": base_backend,
        "llm_used": bool(llm_used),
        "llm_role": llm_role_effective,
        "candidates": candidate_payload,
        "final": final_obj,
    }
