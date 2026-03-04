"""
Precision merge for overlapped ASR chunks (text_accu).

This module implements a CapsWriter-inspired "precision" merge mode using
time-windowed SequenceMatcher alignment. Xiyu backends do not provide stable
token timestamps across all models, so we approximate timestamps at the
character level (linear interpolation over a chunk's time range).

Goal:
- reduce duplicated text around overlap boundaries more aggressively than
  `merge_by_text` (which is robust but purely text-based)
- keep the implementation dependency-free (stdlib only)
"""

from __future__ import annotations

import difflib
from typing import Iterable, List, Tuple

__all__ = [
    "linear_chars_with_timestamps",
    "merge_chars_by_sequence_matcher",
    "chars_to_text",
]


_PUNCT_OR_SPACE = set(
    " \t\r\n"
    ",.?!:;()[]{}<>"
    "\"'`"
    "，。？！：；、（）【】《》〈〉「」『』"
    "“”‘’…—"
)


def linear_chars_with_timestamps(text: str, *, start_s: float, end_s: float) -> Tuple[List[str], List[float]]:
    """Convert text into per-character tokens with linearly interpolated timestamps.

    Args:
        text: Chunk text.
        start_s: Chunk start time in seconds (global timeline).
        end_s: Chunk end time in seconds (global timeline).

    Returns:
        (chars, timestamps) where len(chars) == len(timestamps).
    """
    s = str(text or "")
    if not s:
        return [], []

    start = float(start_s)
    end = float(end_s)
    if end < start:
        end = start

    n = len(s)
    if n == 1 or end == start:
        return list(s), [start] * n

    dur = end - start
    denom = float(n - 1)
    chars = list(s)
    ts = [start + dur * (i / denom) for i in range(n)]
    return chars, ts


def merge_chars_by_sequence_matcher(
    prev_chars: List[str],
    prev_ts: List[float],
    new_chars: List[str],
    new_ts: List[float],
    *,
    offset_s: float,
    overlap_s: float,
    is_first_segment: bool = False,
    lookback_s: float = 1.0,
    lookahead_s: float = 1.0,
    min_match_chars: int = 2,
    fallback_epsilon_s: float = 0.05,
) -> Tuple[List[str], List[float]]:
    """Merge new char tokens into previous tokens with time-window alignment.

    The overlap region is approximated by timestamps:
    - prev overlap: last `lookback_s` seconds before `offset_s`
    - new overlap: first `overlap_s + lookahead_s` seconds starting at `offset_s`

    If a sufficiently long exact match is found, we cut prev at match start and
    append new from its match start (CapsWriter-style).
    """
    if is_first_segment or not prev_chars:
        return list(new_chars), list(new_ts)
    if not new_chars:
        return list(prev_chars), list(prev_ts)
    if len(prev_chars) != len(prev_ts) or len(new_chars) != len(new_ts):
        raise ValueError("chars and timestamps must have equal lengths")

    offset = float(offset_s)
    overlap = max(0.0, float(overlap_s))

    # Our timestamps are only an approximation (linear per char), so a fixed 1s lookback/lookahead
    # can easily select too few characters. Scale the search window with overlap to make matching
    # stable for long-audio chunking (typically 0.5s~3s overlap).
    effective_lookback_s = max(float(lookback_s), overlap + 0.5)
    effective_lookahead_s = max(float(lookahead_s), overlap + 0.5)

    # 1) Select overlap windows (by time) to reduce false matches.
    prev_start_time = offset - effective_lookback_s
    prev_start_idx = 0
    for i, t in enumerate(prev_ts):
        if t >= prev_start_time:
            prev_start_idx = i
            break
    prev_overlap_text = "".join(prev_chars[prev_start_idx:])

    new_end_time = offset + overlap + effective_lookahead_s
    new_end_idx = len(new_ts)
    for i, t in enumerate(new_ts):
        if t > new_end_time:
            new_end_idx = i
            break
    new_overlap_text = "".join(new_chars[:new_end_idx])

    # 2) SequenceMatcher alignment (exact match).
    match = difflib.SequenceMatcher(None, prev_overlap_text, new_overlap_text).find_longest_match(
        0, len(prev_overlap_text), 0, len(new_overlap_text)
    )

    if match.size >= int(min_match_chars):
        prev_cut_idx = prev_start_idx + match.a
        new_start_idx = match.b
        merged_chars = prev_chars[:prev_cut_idx] + new_chars[new_start_idx:]
        merged_ts = prev_ts[:prev_cut_idx] + new_ts[new_start_idx:]
        return _cleanup_repeats(merged_chars, merged_ts)

    # 3) Fallback: time-based dedupe.
    last_time = prev_ts[-1] if prev_ts else offset
    new_start_idx = 0
    for i, t in enumerate(new_ts):
        if t > last_time + float(fallback_epsilon_s):
            new_start_idx = i
            break
    else:
        new_start_idx = len(new_chars)

    merged_chars = prev_chars + new_chars[new_start_idx:]
    merged_ts = prev_ts + new_ts[new_start_idx:]
    return _cleanup_repeats(merged_chars, merged_ts)


def chars_to_text(chars: Iterable[str]) -> str:
    return "".join(chars)


def _cleanup_repeats(chars: List[str], ts: List[float]) -> Tuple[List[str], List[float]]:
    """Remove obvious duplication artifacts (double spaces, repeated punctuation)."""
    if not chars:
        return [], []

    out_chars: List[str] = []
    out_ts: List[float] = []

    for ch, t in zip(chars, ts):
        if out_chars:
            prev = out_chars[-1]
            if ch.isspace() and prev.isspace():
                continue
            if ch in _PUNCT_OR_SPACE and prev == ch:
                continue
        out_chars.append(ch)
        out_ts.append(float(t))

    return out_chars, out_ts
