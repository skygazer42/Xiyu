"""Hotword canonical/alias parsing + auto variants generation.

This module implements the "enterprise hotwords" behavior:
- Each hotword line may optionally contain aliases:
    canonical | alias1 | alias2
  The output replacement must always use `canonical`; aliases are only used for
  recall/matching.
- For some canonical hotwords (esp. containing digits / version numbers), we
  generate a small bounded set of common spoken variants (e.g. "2.0" -> "二点零")
  to improve phoneme-level matching without requiring users to hand-maintain
  every variant.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Set, Tuple

__all__ = [
    "parse_hotword_line",
    "nfkc_normalize",
    "generate_hotword_variants",
]


_PIPE_ALIASES = ("|", "｜")

_ZH_DIGITS = {
    "0": "零",
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
}


def nfkc_normalize(s: str) -> str:
    """Normalize a string using NFKC (full-width -> half-width, etc.)."""
    return unicodedata.normalize("NFKC", str(s or ""))


def parse_hotword_line(line: str) -> Tuple[str, List[str]]:
    """Parse a hotword config line into (canonical, aliases).

    Supported formats:
      - canonical
      - canonical | alias1 | alias2

    Notes:
    - Whitespace around separators is ignored.
    - Empty parts are dropped.
    """
    s = str(line or "").strip()
    if not s:
        return "", []

    # Normalize alternative full-width pipes.
    for p in _PIPE_ALIASES[1:]:
        s = s.replace(p, "|")

    if "|" not in s:
        return s, []

    parts = [p.strip() for p in s.split("|")]
    parts = [p for p in parts if p]
    if not parts:
        return "", []
    canonical = parts[0]
    aliases = parts[1:]
    return canonical, aliases


def _replace_dot_to_dian(s: str) -> str:
    # Covers both ASCII '.' and common full-width dot variants.
    return (
        str(s)
        .replace(".", "点")
        .replace("．", "点")
        .replace("·", "点")
    )


def _replace_digits_to_zh(s: str) -> str:
    return "".join(_ZH_DIGITS.get(ch, ch) for ch in str(s))


_HAS_DIGIT_RE = re.compile(r"\d")


def generate_hotword_variants(canonical: str, *, max_variants: int = 20) -> Set[str]:
    """Generate a small set of stable variants for a canonical hotword.

    Current variants:
    - NFKC normalized form (helps with full-width digits/punctuation)
    - For digit-containing terms, common "version number" spoken forms:
      - '.' -> '点'
      - digits -> Chinese digits
      - digits + '.' combined
    """
    raw = str(canonical or "").strip()
    if not raw:
        return set()

    out: List[str] = []

    def _add(v: str) -> None:
        vv = str(v or "").strip()
        if not vv:
            return
        if vv in out:
            return
        out.append(vv)

    _add(raw)

    nfkc = nfkc_normalize(raw).strip()
    if nfkc and nfkc != raw:
        _add(nfkc)

    base = nfkc or raw

    if _HAS_DIGIT_RE.search(base):
        _add(_replace_dot_to_dian(base))
        _add(_replace_digits_to_zh(base))
        _add(_replace_digits_to_zh(_replace_dot_to_dian(base)))

    # Safety cap: keep output bounded even if future rules add more variants.
    if max_variants > 0 and len(out) > int(max_variants):
        out = out[: int(max_variants)]

    return set(out)

