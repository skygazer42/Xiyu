"""
统一文本后处理器

整合 ITN、繁简转换、标点转换、填充词移除、全角归一化、中英文间距等功能。
支持并行处理多个文本。
"""

__all__ = ['TextPostProcessor', 'PostProcessorSettings']

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List
from dataclasses import dataclass
import re

from .chinese_itn import ChineseITN
from .zh_convert import ZhConverter
from .punctuation import PunctuationConverter, FullwidthNormalizer, merge_punctuation
from .filler_remover import FillerRemover
from .spacing import SpacingProcessor
from .gov_formatter import format_gov_numbers


@dataclass
class PostProcessorSettings:
    """后处理配置"""
    # 填充词移除
    filler_remove_enable: bool = False
    filler_aggressive: bool = False

    # 全角字符归一化 (QJ2BJ)
    qj2bj_enable: bool = True

    # ITN (中文数字格式化)
    itn_enable: bool = True
    itn_erhua_remove: bool = False

    # 中英文间距
    spacing_cjk_ascii_enable: bool = False

    # 口述标点指令（dictation）
    spoken_punc_enable: bool = False

    # 英文缩写合并：A I -> AI, V S Code -> VS Code
    acronym_merge_enable: bool = False

    # 政务会议数字/格式模板化（在 ITN 之后执行）
    # - 2025年5月7日/号 -> 2025-05-07
    # - 常政办发[2025] 12 号 -> 常政办发〔2025〕12号
    # - 1.2 亿 元 -> 1.2亿元
    gov_format_enable: bool = True

    # 繁简转换
    zh_convert_enable: bool = False
    zh_convert_locale: str = "zh-hans"

    # 标点转换
    punc_convert_enable: bool = False
    punc_add_space: bool = True

    # 标点恢复
    punc_restore_enable: bool = False
    punc_restore_model: str = "ct-punc-c"
    punc_restore_device: str = "cpu"

    # 标点合并
    punc_merge_enable: bool = False

    # 末尾标点移除 (用于实时转写场景)
    trash_punc_enable: bool = False
    trash_punc_chars: str = "，。,."


class TextPostProcessor:
    """
    统一文本后处理器

    整合多个后处理功能，按固定顺序执行：
    1. 填充词移除 (先移除噪声)
    2. 全角归一化 (统一字符编码)
    3. ITN - 中文数字转阿拉伯数字
    4. 中英文间距 (在 ITN 后处理转换后的数字)
    5. 繁简转换
    6. 标点转换 (最后格式化)

    用法:
        settings = PostProcessorSettings(itn_enable=True)
        processor = TextPostProcessor(settings)
        result = processor.process("今天是二零二五年一月三十日")
        # result: "今天是2025年1月30日"
    """

    def __init__(self, settings: Optional[PostProcessorSettings] = None):
        """
        初始化后处理器

        Args:
            settings: 后处理配置，为 None 时使用默认配置
        """
        if settings is None:
            settings = PostProcessorSettings()

        self.settings = settings
        self._spoken_punc_enable = bool(getattr(settings, "spoken_punc_enable", False))
        self._acronym_merge_enable = bool(getattr(settings, "acronym_merge_enable", False))
        self._gov_format_enable = bool(getattr(settings, "gov_format_enable", True))

        # 按需初始化各组件
        self.filler_remover = (
            FillerRemover(aggressive=settings.filler_aggressive)
            if settings.filler_remove_enable
            else None
        )
        self.fullwidth_normalizer = (
            FullwidthNormalizer()
            if settings.qj2bj_enable
            else None
        )
        self.itn = ChineseITN(erhua_remove=settings.itn_erhua_remove) if settings.itn_enable else None
        self.spacing_processor = (
            SpacingProcessor()
            if settings.spacing_cjk_ascii_enable
            else None
        )
        self.zh_converter = ZhConverter() if settings.zh_convert_enable else None
        self.punc_converter = (
            PunctuationConverter(add_space=settings.punc_add_space)
            if settings.punc_convert_enable
            else None
        )
        self._punc_restorer = None
        self._punc_restore_enable = settings.punc_restore_enable
        self._punc_restore_model = settings.punc_restore_model
        self._punc_restore_device = settings.punc_restore_device
        self.punc_merge_enable = settings.punc_merge_enable
        self.trash_punc_enable = settings.trash_punc_enable
        self.trash_punc_chars = settings.trash_punc_chars

        # Compiled regex for acronym merging.
        #
        # We merge single-char tokens separated by whitespace:
        #   - Letters: "A I" -> "AI"
        #   - Letters + digits: "Q W E N 3" -> "QWEN3", "H 2 O" -> "H2O"
        #
        # Guard rails:
        # - Require the first token to be a letter (avoid merging digit-only sequences like "2 0 2 6").
        # - Use word-boundary-style lookarounds to avoid merging inside longer alnum strings.
        self._acronym_seq_re = re.compile(
            r"(?<![A-Za-z0-9])(?:[A-Za-z](?:\s+[A-Za-z0-9]){1,})(?![A-Za-z0-9])"
        )

        # Dictation spoken punctuation commands (conservative: only applied at start/end).
        self._spoken_punc_map = {
            "逗号": "，",
            "句号": "。",
            "问号": "？",
            "感叹号": "！",
            "回车": "\n",
            "换行": "\n",
        }
        # Stable iteration order for peeling commands from boundaries.
        self._spoken_punc_cmds = list(self._spoken_punc_map.keys())

    def _apply_spoken_punctuation_commands(self, text: str) -> str:
        if not self._spoken_punc_enable or not text:
            return text

        out = text

        # Peel prefix commands (allow spaces/tabs around commands).
        prefix = ""
        while True:
            s = out.lstrip(" \t")
            matched = None
            for cmd in self._spoken_punc_cmds:
                if s.startswith(cmd):
                    matched = cmd
                    break
            if matched is None:
                break
            prefix += self._spoken_punc_map.get(matched, matched)
            out = s[len(matched) :]

        # Peel suffix commands (allow spaces/tabs around commands).
        suffix = ""
        while True:
            s = out.rstrip(" \t")
            matched = None
            for cmd in self._spoken_punc_cmds:
                if s.endswith(cmd):
                    matched = cmd
                    break
            if matched is None:
                break
            suffix = self._spoken_punc_map.get(matched, matched) + suffix
            out = s[: -len(matched)]

        return prefix + out + suffix

    def _merge_english_acronyms(self, text: str) -> str:
        if not self._acronym_merge_enable or not text:
            return text

        def _repl(m: re.Match) -> str:
            return re.sub(r"\s+", "", m.group(0))

        return self._acronym_seq_re.sub(_repl, text)

    @property
    def punc_restorer(self):
        """懒加载标点恢复器"""
        if self._punc_restorer is None and self._punc_restore_enable:
            try:
                from .punctuation_restorer import PunctuationRestorer
                self._punc_restorer = PunctuationRestorer(
                    model=self._punc_restore_model,
                    device=self._punc_restore_device,
                )
            except Exception:
                self._punc_restore_enable = False
        return self._punc_restorer

    def process(self, text: str) -> str:
        """
        执行文本后处理

        处理顺序:
        1. 填充词移除 (先移除噪声)
        2. 全角归一化 (统一字符编码)
        3. 标点恢复 (如果启用)
        4. ITN - 中文数字转阿拉伯数字
        5. 中英文间距 (在 ITN 后处理)
        6. 繁简转换
        7. 标点转换 (最后格式化)
        8. 标点合并 (清理重复/混合标点)

        Args:
            text: 输入文本

        Returns:
            处理后的文本
        """
        if not text:
            return text

        # 1. 填充词移除
        if self.filler_remover:
            text = self.filler_remover.remove(text)

        # 2. 全角归一化 (QJ2BJ)
        if self.fullwidth_normalizer:
            text = self.fullwidth_normalizer.normalize(text)

        # 2.5 口述标点指令（可选，dictation 场景）
        text = self._apply_spoken_punctuation_commands(text)

        # 3. 标点恢复 (在 ITN 之前，确保数字转换正确)
        if self._punc_restore_enable and self.punc_restorer:
            text = self.punc_restorer.restore(text)

        # 4. 中文数字格式化
        if self.itn:
            text = self.itn.convert(text)

        # 4.2 政务会议数字/格式模板化（在 ITN 之后，避免中文数字未转换导致规则漏匹配）
        if self._gov_format_enable:
            text = format_gov_numbers(text)

        # 4.5 英文缩写合并（在 spacing 之前）
        text = self._merge_english_acronyms(text)

        # 5. 中英文间距
        if self.spacing_processor:
            text = self.spacing_processor.add_spacing(text)

        # 6. 繁简转换
        if self.zh_converter:
            text = self.zh_converter.convert(text, self.settings.zh_convert_locale)

        # 7. 标点转换
        if self.punc_converter:
            text = self.punc_converter.to_half(text)

        # 8. 标点合并 (清理重复/混合标点)
        if self.punc_merge_enable:
            text = merge_punctuation(text)

        # 9. 末尾标点移除 (最后一步)
        if self.trash_punc_enable and text:
            text = text.rstrip(self.trash_punc_chars)

        return text

    def process_final(self, text: str) -> str:
        """Final formatting stage for *already-polished* text (e.g. after LLM).

        We intentionally avoid:
        - filler removal (content deletion)
        - punctuation restoration models (extra latency + non-determinism)

        The goal is to make numeric templates stable (ITN + gov formatting),
        without "rewriting" content.
        """
        if not text:
            return text

        out = text

        if self.fullwidth_normalizer:
            out = self.fullwidth_normalizer.normalize(out)

        out = self._apply_spoken_punctuation_commands(out)

        if self.itn:
            out = self.itn.convert(out)

        if self._gov_format_enable:
            out = format_gov_numbers(out)

        out = self._merge_english_acronyms(out)

        if self.spacing_processor:
            out = self.spacing_processor.add_spacing(out)

        if self.zh_converter:
            out = self.zh_converter.convert(out, self.settings.zh_convert_locale)

        if self.punc_converter:
            out = self.punc_converter.to_half(out)

        if self.punc_merge_enable:
            out = merge_punctuation(out)

        if self.trash_punc_enable and out:
            out = out.rstrip(self.trash_punc_chars)

        return out

    def process_filler_remove(self, text: str) -> str:
        """仅执行填充词移除"""
        if not text or not self.filler_remover:
            return text
        return self.filler_remover.remove(text)

    def process_qj2bj(self, text: str) -> str:
        """仅执行全角归一化"""
        if not text or not self.fullwidth_normalizer:
            return text
        return self.fullwidth_normalizer.normalize(text)

    def process_itn(self, text: str) -> str:
        """仅执行 ITN 转换"""
        if not text or not self.itn:
            return text
        return self.itn.convert(text)

    def process_spacing(self, text: str) -> str:
        """仅执行中英文间距"""
        if not text or not self.spacing_processor:
            return text
        return self.spacing_processor.add_spacing(text)

    def process_zh_convert(self, text: str, locale: Optional[str] = None) -> str:
        """仅执行繁简转换"""
        if not text or not self.zh_converter:
            return text
        locale = locale or self.settings.zh_convert_locale
        return self.zh_converter.convert(text, locale)

    def process_punctuation(self, text: str, to_half: bool = True) -> str:
        """仅执行标点转换"""
        if not text or not self.punc_converter:
            return text
        return self.punc_converter.to_half(text) if to_half else self.punc_converter.to_full(text)

    @classmethod
    def from_config(cls, config) -> 'TextPostProcessor':
        """
        从配置对象创建后处理器

        Args:
            config: 包含后处理配置的对象 (如 Settings)

        Returns:
            TextPostProcessor 实例
        """
        settings = PostProcessorSettings(
            filler_remove_enable=getattr(config, 'filler_remove_enable', False),
            filler_aggressive=getattr(config, 'filler_aggressive', False),
            qj2bj_enable=getattr(config, 'qj2bj_enable', True),
            itn_enable=getattr(config, 'itn_enable', True),
            itn_erhua_remove=getattr(config, 'itn_erhua_remove', False),
            spacing_cjk_ascii_enable=getattr(config, 'spacing_cjk_ascii_enable', False),
            spoken_punc_enable=getattr(config, 'spoken_punc_enable', False),
            acronym_merge_enable=getattr(config, 'acronym_merge_enable', False),
            gov_format_enable=getattr(config, 'gov_format_enable', True),
            zh_convert_enable=getattr(config, 'zh_convert_enable', False),
            zh_convert_locale=getattr(config, 'zh_convert_locale', 'zh-hans'),
            punc_convert_enable=getattr(config, 'punc_convert_enable', False),
            punc_add_space=getattr(config, 'punc_add_space', True),
            punc_restore_enable=getattr(config, 'punc_restore_enable', False),
            punc_restore_model=getattr(config, 'punc_restore_model', 'ct-punc-c'),
            punc_restore_device=getattr(config, 'device', 'cpu'),
            punc_merge_enable=getattr(config, 'punc_merge_enable', False),
            trash_punc_enable=getattr(config, 'trash_punc_enable', False),
            trash_punc_chars=getattr(config, 'trash_punc_chars', '，。,.'),
        )
        return cls(settings)

    def process_batch(self, texts: List[str], max_workers: int = 4) -> List[str]:
        """
        批量并行处理多个文本

        使用线程池并行处理多个文本，提升批量处理效率。

        Args:
            texts: 文本列表
            max_workers: 最大工作线程数

        Returns:
            处理后的文本列表
        """
        if not texts:
            return []

        if len(texts) == 1:
            return [self.process(texts[0])]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(self.process, texts))

        return results

    async def process_batch_async(self, texts: List[str], max_concurrent: int = 4) -> List[str]:
        """
        异步批量处理多个文本

        Args:
            texts: 文本列表
            max_concurrent: 最大并发数

        Returns:
            处理后的文本列表
        """
        if not texts:
            return []

        if len(texts) == 1:
            return [self.process(texts[0])]

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            tasks = [loop.run_in_executor(executor, self.process, text) for text in texts]
            results = await asyncio.gather(*tasks)

        return list(results)


if __name__ == '__main__':
    # 测试 - 所有功能
    print("=== 完整后处理测试 ===")
    settings = PostProcessorSettings(
        filler_remove_enable=True,
        filler_aggressive=False,
        qj2bj_enable=True,
        itn_enable=True,
        spacing_cjk_ascii_enable=True,
        punc_convert_enable=True,
    )
    processor = TextPostProcessor(settings)

    test_cases = [
        "呃那个今天是二零二五年一月三十日",
        "就是说AI技术很厉害",
        "ＡＢＣＤ１２３４",
        "价格是三百五十元，百分之五十折扣",
        "Python3编程，使用TensorFlow",
    ]

    for text in test_cases:
        result = processor.process(text)
        print(f"{text!r}")
        print(f"  → {result!r}")
        print()
