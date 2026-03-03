"""Subtitle export helpers (SRT / WebVTT).

Backend endpoints primarily return structured timeline data (`sentences` /
`speaker_turns`). This module converts that structure into subtitle text formats
for convenience and for non-web clients.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def format_srt_time(ms: int) -> str:
    """Format milliseconds into SRT time: HH:MM:SS,mmm."""
    if ms < 0:
        ms = 0
    hours = ms // 3_600_000
    minutes = (ms % 3_600_000) // 60_000
    seconds = (ms % 60_000) // 1_000
    milliseconds = ms % 1_000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _speaker_prefix(seg: Dict[str, Any]) -> str:
    speaker = str(seg.get("speaker") or "").strip()
    if speaker:
        return f"[{speaker}] "

    spk_id = seg.get("speaker_id")
    if isinstance(spk_id, int) and spk_id >= 0:
        return f"[说话人{spk_id + 1}] "

    return ""


def pick_timeline_segments(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pick the best timeline segments from a TingWu-style response dict.

    Priority:
    1. `sentences` (preferred: usually more fine-grained, best for subtitles)
    2. `speaker_turns` (fallback: still time-aligned and speaker-labeled)
    """
    if not isinstance(obj, dict):
        return []

    sentences = obj.get("sentences")
    if isinstance(sentences, list) and sentences:
        return [s for s in sentences if isinstance(s, dict)]

    turns = obj.get("speaker_turns")
    if isinstance(turns, list) and turns:
        return [t for t in turns if isinstance(t, dict)]

    return []


def generate_srt(segments: Sequence[Dict[str, Any]]) -> str:
    """Generate SRT content from timeline segments.

    Each segment should have:
    - start (ms)
    - end (ms)
    - text (str)
    - speaker / speaker_id (optional)
    """
    lines: List[str] = []
    cue_index = 1

    for seg in segments:
        if not isinstance(seg, dict):
            continue

        start = _safe_int(seg.get("start"), 0)
        end = _safe_int(seg.get("end"), start)
        if end < start:
            end = start

        text = str(seg.get("text") or "").strip()
        if not text:
            continue

        lines.append(str(cue_index))
        lines.append(f"{format_srt_time(start)} --> {format_srt_time(end)}")
        lines.append(f"{_speaker_prefix(seg)}{text}")
        lines.append("")
        cue_index += 1

    return "\n".join(lines).strip()


def generate_srt_from_result(obj: Dict[str, Any]) -> Optional[str]:
    """Generate SRT content from a TingWu-style response dict."""
    segments = pick_timeline_segments(obj)
    if not segments:
        return None

    srt = generate_srt(segments)
    return srt if srt.strip() else None

