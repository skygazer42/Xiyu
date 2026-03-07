"""
文本后处理模块

提供语音识别后的文本优化功能：
- 填充词移除 (filler removal)
- 全角字符归一化 (QJ2BJ)
- 中文数字格式化 (ITN)
- 中英文间距 (CJK-ASCII spacing)
- 繁简转换 (zh conversion)
- 标点转换 (punctuation conversion)
- 流式文本去重 (stream deduplication)
"""

from .chinese_itn import ChineseITN, remove_erhua
from .zh_convert import ZhConverter, convert, issimp
from .punctuation import (
    PunctuationConverter,
    FullwidthNormalizer,
    convert_full_to_half,
    convert_half_to_full,
    normalize_fullwidth,
    merge_punctuation,
)
from .filler_remover import FillerRemover, remove_fillers
from .spacing import SpacingProcessor, add_cjk_ascii_spacing
from .text_corrector import TextCorrector
from .punctuation_restorer import PunctuationRestorer
from .post_processor import TextPostProcessor, PostProcessorSettings
from .gov_formatter import format_gov_numbers
from .stream_merger import StreamTextMerger

__all__ = [
    # 填充词移除
    'FillerRemover',
    'remove_fillers',
    # 全角归一化
    'FullwidthNormalizer',
    'normalize_fullwidth',
    # ITN
    'ChineseITN',
    'remove_erhua',
    # 中英文间距
    'SpacingProcessor',
    'add_cjk_ascii_spacing',
    # 繁简转换
    'ZhConverter',
    'convert',
    'issimp',
    # 标点转换
    'PunctuationConverter',
    'convert_full_to_half',
    'convert_half_to_full',
    'merge_punctuation',
    # 标点恢复
    'PunctuationRestorer',
    # 通用文本纠错
    'TextCorrector',
    # 统一后处理器
    'TextPostProcessor',
    'PostProcessorSettings',
    # 政务会议数字/格式模板化
    'format_gov_numbers',
    # 流式去重
    'StreamTextMerger',
]
