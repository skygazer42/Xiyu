"""Word/char-level timestamps built from character timestamps.

Xiyu's long-audio chunking merges transcript text across overlapping chunks and
approximates per-character timestamps by linear interpolation inside each chunk.

This module aggregates those (char, ts) pairs into word-level tokens suitable
for:
- locating / highlighting in players
- lightweight search indexing

It is best-effort and is NOT intended to be sample-accurate.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

__all__ = ["build_word_timestamps"]


_TOKEN_SPAN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")

_PUNCT_OR_SPACE = set(
    " \t\r\n"
    ",.?!:;()[]{}<>"
    "\"'`"
    "，。？！：；、（）【】《》〈〉「」『』"
    "“”‘’…—"
)


def _tokenize_mixed(text: str) -> List[Tuple[str, int, int]]:
    """Tokenize mixed CJK + ASCII into (token, start, end) char spans."""
    s = str(text or "")
    if not s:
        return []

    tokens: List[Tuple[str, int, int]] = []
    for m in _TOKEN_SPAN_RE.finditer(s):
        frag = m.group(0)
        start = int(m.start())
        end = int(m.end())
        if not frag:
            continue

        # ASCII run: keep as-is.
        if frag[0].isascii():
            tokens.append((frag, start, end))
            continue

        # CJK run: try jieba segmentation, fall back to per-char.
        try:
            import jieba  # type: ignore

            for word, w_start, w_end in jieba.tokenize(frag, mode="default"):
                w = str(word or "").strip()
                if not w:
                    continue
                tokens.append((w, start + int(w_start), start + int(w_end)))
        except Exception:
            for i, ch in enumerate(frag):
                if ch in _PUNCT_OR_SPACE or ch.isspace():
                    continue
                tokens.append((ch, start + i, start + i + 1))

    return tokens


def build_word_timestamps(
    text: str,
    char_ts_s: List[float],
    *,
    level: str = "word",
    max_words: int = 20000,
) -> List[Dict[str, int | str]]:
    """Build word/char timestamps from per-character timestamps.

    Args:
        text: Transcript text (must match the char_ts length).
        char_ts_s: Per-character timestamps in seconds (len == len(text)).
        level: "word" or "char".
        max_words: Output guard to avoid huge responses for multi-hour meetings.

    Returns:
        List of {text,start,end} in milliseconds.
    """
    s = str(text or "")
    if not s:
        return []

    if not isinstance(char_ts_s, list) or len(char_ts_s) != len(s):
        return []

    lvl = str(level or "word").strip().lower()
    if lvl not in ("word", "char"):
        lvl = "word"

    try:
        limit = int(max_words)
    except Exception:
        limit = 20000
    if limit <= 0:
        limit = 20000

    if lvl == "char":
        tokens = [
            (ch, i, i + 1)
            for i, ch in enumerate(s)
            if (ch not in _PUNCT_OR_SPACE and not ch.isspace())
        ]
    else:
        tokens = _tokenize_mixed(s)

    out: List[Dict[str, int | str]] = []
    prev_start = 0
    prev_end = 0

    for tok, start, end in tokens:
        if len(out) >= limit:
            break
        if end <= start:
            continue
        if start < 0:
            continue
        if end > len(s):
            continue

        try:
            start_s = float(char_ts_s[start])
            end_s = float(char_ts_s[end - 1])
        except Exception:
            continue

        start_ms = int(round(start_s * 1000.0))
        end_ms = int(round(end_s * 1000.0))
        if end_ms < start_ms:
            end_ms = start_ms

        # Best-effort monotonicity guard.
        if start_ms < prev_start:
            start_ms = prev_start
        if end_ms < prev_end:
            end_ms = prev_end

        out.append({"text": tok, "start": start_ms, "end": end_ms})
        prev_start = start_ms
        prev_end = end_ms

    return out

