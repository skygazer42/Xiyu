# coding: utf-8
"""Gov/enterprise meeting number & format normalization.

This module is intentionally *conservative* and deterministic. It is meant to
run after ITN (Chinese numerals -> Arabic) so patterns like:

- 二零二五年五月七日 -> 2025年5月7日
- 常政办发〔二〇二五〕十二号 -> 常政办发〔2025〕12号

can be further normalized into enterprise-friendly templates:

- 2025年5月7日/号 -> 2025-05-07
- 常政办发[2025] 12 号 -> 常政办发〔2025〕12号
- 1.2 亿 元 -> 1.2亿元

We keep the scope narrow to avoid unwanted "rewriting".
"""

from __future__ import annotations

import re
from typing import Match, Optional

__all__ = ["format_gov_numbers"]


def _safe_int(s: str) -> Optional[int]:
    try:
        return int(str(s))
    except Exception:
        return None


# 2025年5月7日 / 2025年05月07号 -> 2025-05-07
_DATE_CN_YMD_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*(?:日|号)(?!\d)"
)

# 2025/5/7, 2025.5.7, 2025-5-7 -> 2025-05-07
_DATE_SEP_YMD_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*[-/.]\s*(?P<month>\d{1,2})\s*[-/.]\s*(?P<day>\d{1,2})(?!\d)"
)


def _fmt_ymd(year: str, month: str, day: str) -> Optional[str]:
    y = _safe_int(year)
    m = _safe_int(month)
    d = _safe_int(day)
    if y is None or m is None or d is None:
        return None
    if y < 1900 or y > 2100:
        return None
    if m < 1 or m > 12:
        return None
    if d < 1 or d > 31:
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


# 常政办发〔2025〕12号 / 常政办发[2025] 12 号 -> 常政办发〔2025〕12号
# Keep it strict: require a 4-digit year and trailing "号".
_DOC_NO_RE = re.compile(
    r"(?P<prefix>[\u4e00-\u9fffA-Za-z]{2,30})\s*"
    r"(?P<lbracket>[〔\[\【\(\（])\s*(?P<year>\d{4})\s*(?P<rbracket>[〕\]\】\)\）])\s*"
    r"(?P<num>\d{1,4})\s*号"
)


def _normalize_doc_no(m: Match[str]) -> str:
    prefix = str(m.group("prefix") or "").strip()
    year = str(m.group("year") or "").strip()
    num_raw = str(m.group("num") or "").strip()
    num_i = _safe_int(num_raw)
    if num_i is None:
        num = num_raw
    else:
        # 0012 -> 12, but keep 0 if it's literally "0".
        num = str(num_i)
    return f"{prefix}〔{year}〕{num}号"


# Money/percent spacing cleanup (do NOT change numeric value).
_PERCENT_SPACE_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?:%|％)")
_MONEY_YUAN_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*元")
_MONEY_WANYI_YUAN_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>万|亿)\s*元")


# 事项编码 320400 123456 -> 事项编码：320400123456
_ITEM_CODE_RE = re.compile(
    r"(?P<label>事项编码|事项编号|事项代码)\s*[:：]?\s*(?P<code>[0-9A-Za-z][0-9A-Za-z\-\s]{5,})"
)


def _normalize_item_code(m: Match[str]) -> str:
    label = str(m.group("label") or "").strip() or "事项编码"
    code = str(m.group("code") or "")
    code = re.sub(r"\s+", "", code)
    return f"{label}：{code}"


def format_gov_numbers(text: str) -> str:
    """Normalize gov-meeting-friendly number templates.

    This function is designed to be idempotent and safe to run multiple times.
    """
    if not text:
        return text

    out = str(text)

    def _repl_cn_date(m: Match[str]) -> str:
        iso = _fmt_ymd(m.group("year"), m.group("month"), m.group("day"))
        return iso or m.group(0)

    def _repl_sep_date(m: Match[str]) -> str:
        iso = _fmt_ymd(m.group("year"), m.group("month"), m.group("day"))
        return iso or m.group(0)

    out = _DATE_CN_YMD_RE.sub(_repl_cn_date, out)
    out = _DATE_SEP_YMD_RE.sub(_repl_sep_date, out)

    out = _DOC_NO_RE.sub(_normalize_doc_no, out)

    # Percent (remove any accidental spacing).
    out = _PERCENT_SPACE_RE.sub(lambda m: f"{m.group('num')}%", out)

    # Money: merge spaces inside "万/亿 元" first, then plain "元".
    out = _MONEY_WANYI_YUAN_RE.sub(lambda m: f"{m.group('num')}{m.group('unit')}元", out)
    out = _MONEY_YUAN_RE.sub(lambda m: f"{m.group('num')}元", out)

    out = _ITEM_CODE_RE.sub(_normalize_item_code, out)

    return out

