"""Multi-backend (all-model) transcription + LLM fusion (v2).

Why v2?
- The original ensemble implementation includes the base backend transcript in
  the multi-ASR references passed to the LLM. For long audio this can dominate
  the prompt budget and crowd out other backends (the ones that actually help
  correct base mistakes).
- It also feeds full-length reference texts, which are often truncated and
  become less useful for later turns.

This v2 variant:
- Excludes the base backend from references (base turns are already provided).
- Builds *time-windowed* reference snippets per LLM call, using candidate
  sentence timestamps when available.
- Optionally splits polishing into turn batches to avoid blowing the LLM
  context window.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.config import settings
from src.core.speaker import SpeakerLabeler
from src.core.llm.roles import get_role
from src.core.text_processor.post_processor import PostProcessorSettings, TextPostProcessor

# Reuse the well-tested HTTP + parsing helpers from the original module.
from src.core.ensemble import (  # noqa: PLC0415
    CandidateResult,
    get_ensemble_targets,
    _call_xiyu_transcribe,
    _clean_text_for_llm,
    _llm_polish_turn_texts,
    _merge_asr_options,
    _now_ms,
    _pick_base_result,
)

logger = logging.getLogger(__name__)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        # Some backends may emit floats in seconds; we keep best-effort.
        if isinstance(value, (int, float)):
            return int(value)
        return int(str(value))
    except Exception:
        return int(default)


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    # Treat empty/invalid ranges as non-overlapping.
    if a_end <= a_start or b_end <= b_start:
        return False
    return a_start < b_end and a_end > b_start


def _turn_window_ms(turns: List[Any]) -> Tuple[int, int]:
    starts: List[int] = []
    ends: List[int] = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        starts.append(_safe_int(t.get("start"), 0))
        ends.append(_safe_int(t.get("end"), 0))
    if not starts or not ends:
        return 0, 0
    return min(starts), max(ends)


def _trim_text_middle(text: str, max_chars: int) -> str:
    t = str(text or "").strip()
    if max_chars <= 0:
        return ""
    if len(t) <= max_chars:
        return t
    half = max_chars // 2
    head = t[:half].rstrip()
    tail = t[-half:].lstrip()
    return f"{head}\n...\n{tail}"


def _extract_window_reference(obj: Dict[str, Any], start_ms: int, end_ms: int) -> str:
    """Extract a candidate reference text for the given time window.

    Preference:
    1) speaker_turns (if present)
    2) sentences with timestamps
    3) empty (caller can fall back to cleaned_text)
    """
    turns = obj.get("speaker_turns")
    if isinstance(turns, list) and turns:
        lines: List[str] = []
        for t in turns:
            if not isinstance(t, dict):
                continue
            ts = _safe_int(t.get("start"), 0)
            te = _safe_int(t.get("end"), 0)
            if start_ms and end_ms and not _overlaps(ts, te, start_ms, end_ms):
                continue
            txt = str(t.get("text") or "").strip()
            if not txt:
                continue
            spk = str(t.get("speaker") or "").strip()
            lines.append(f"{spk}: {txt}" if spk else txt)
        if lines:
            return "\n".join(lines)

    sentences = obj.get("sentences")
    if isinstance(sentences, list) and sentences:
        lines = []
        any_with_time = False
        for s in sentences:
            if not isinstance(s, dict):
                continue
            ts = _safe_int(s.get("start"), 0)
            te = _safe_int(s.get("end"), 0)
            if ts or te:
                any_with_time = True
            if start_ms and end_ms and (ts or te) and not _overlaps(ts, te, start_ms, end_ms):
                continue
            txt = str(s.get("text") or "").strip()
            if txt:
                lines.append(txt)
        # If we had timestamps, only return when the window actually captured something.
        if lines and (not any_with_time or (start_ms == 0 and end_ms == 0) or True):
            return "\n".join(lines)

    return ""


async def _llm_polish_turns_in_batches(
    *,
    base_turns: List[Dict[str, Any]],
    candidates: List[CandidateResult],
    base_backend: str,
    role_name: str,
    max_reference_chars: int = 12000,
    max_ref_per_backend_chars: int = 2000,
    turns_per_call: int = 25,
) -> Optional[Dict[int, str]]:
    if not base_turns:
        return None

    if turns_per_call <= 0:
        turns_per_call = 25

    mapping: Dict[int, str] = {}

    for offset in range(0, len(base_turns), turns_per_call):
        chunk_turns = base_turns[offset : offset + turns_per_call]
        win_start, win_end = _turn_window_ms(chunk_turns)

        refs: Dict[str, str] = {}
        for c in candidates:
            if not c.success:
                continue
            if c.backend == base_backend:
                continue
            if not c.result_obj or not isinstance(c.result_obj, dict):
                continue

            window_text = _extract_window_reference(c.result_obj, win_start, win_end)
            if not window_text:
                window_text = c.cleaned_text or ""
            window_text = _trim_text_middle(window_text, max_ref_per_backend_chars)
            if window_text:
                refs[c.backend] = window_text

        try:
            local_map = await _llm_polish_turn_texts(
                base_turns=chunk_turns,
                references=refs,
                role_name=role_name,
                max_reference_chars=max_reference_chars,
            )
        except Exception as e:
            logger.warning("LLM polish batch failed (ignored): %s", e)
            local_map = None

        if not local_map:
            continue

        for local_idx, text in local_map.items():
            global_idx = offset + int(local_idx)
            if global_idx < 0 or global_idx >= len(base_turns):
                continue
            s = str(text or "").strip()
            if not s:
                continue
            mapping[global_idx] = s

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
    """Run multi-model transcription and optionally fuse with LLM (v2)."""
    targets = get_ensemble_targets()

    base_backend = str(getattr(settings, "ensemble_base_backend", "") or "").strip() or "pytorch"
    llm_role_effective = (
        str(llm_role).strip()
        if llm_role is not None and str(llm_role).strip()
        else str(getattr(settings, "ensemble_llm_role", "") or "").strip() or "policy_meeting_aggressive"
    )
    # Ensure response exposes the *actual* role used (get_role() falls back to default).
    try:
        llm_role_used = get_role(llm_role_effective).name
    except Exception:
        llm_role_used = llm_role_effective
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
            # Base from Settings, then override known keys (similar to engine).
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
    base_turns_any = base_obj.get("speaker_turns") if isinstance(base_obj, dict) else None

    # If we asked for speaker turns but didn't get them, skip LLM (still return base).
    if with_speaker and (not isinstance(base_turns_any, list) or not base_turns_any):
        apply_llm = False

    llm_used = False
    polished_map: Optional[Dict[int, str]] = None
    if apply_llm and bool(settings.llm_enable) and isinstance(base_turns_any, list):
        try:
            base_backend_effective = str(getattr(base, "backend", "") or base_backend)
            # Batch polish to avoid context overflow on long meetings.
            polished_map = await _llm_polish_turns_in_batches(
                base_turns=[t for t in base_turns_any if isinstance(t, dict)],
                candidates=candidates,
                base_backend=base_backend_effective,
                role_name=llm_role_used,
            )
            llm_used = polished_map is not None
        except Exception as e:
            logger.warning("LLM polish failed (ignored): %s", e)
            polished_map = None
            llm_used = False

    # Build final result (TranscribeResponse-compatible dict).
    if isinstance(base_turns_any, list) and base_turns_any:
        base_turns: List[Dict[str, Any]] = [t for t in base_turns_any if isinstance(t, dict)]
        final_turns: List[Dict[str, Any]] = []
        for idx, t in enumerate(base_turns):
            t2 = dict(t)
            if polished_map and idx in polished_map:
                t2["text"] = polished_map[idx]
            final_turns.append(t2)

        # Final numeric/template formatting after LLM so the final transcript
        # uses enterprise-stable formats (ISO dates, doc numbers, money units).
        if apply_hotword:
            try:
                for t in final_turns:
                    if t.get("text"):
                        t["text"] = post_processor.process_final(str(t.get("text") or ""))
            except Exception as e:
                logger.warning("Postprocess final for ensemble turns failed (ignored): %s", e)

        label_style = "numeric"
        try:
            speaker_section = (merged_asr_options or {}).get("speaker") if isinstance(merged_asr_options, dict) else None
            if isinstance(speaker_section, dict) and speaker_section.get("label_style"):
                label_style = str(speaker_section.get("label_style") or "numeric")
        except Exception:
            label_style = "numeric"
        speaker_labeler = SpeakerLabeler(label_style=label_style)
        transcript = speaker_labeler.format_transcript(final_turns, include_timestamp=True)

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
            raw_text = "\n".join(str(t.get("text") or "") for t in base_turns).strip()
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
        final_obj = dict(base_obj)
        final_obj["code"] = 0

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
        "llm_role": llm_role_used,
        "candidates": candidate_payload,
        "final": final_obj,
    }
