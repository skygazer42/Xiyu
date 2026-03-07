"""热词纠错器 - 两阶段检索优化版 + 音形义联合纠错

基于 CapsWriter-Offline 的两阶段检索策略：
1. FastRAG 粗筛：使用倒排索引 + Numba JIT 快速过滤候选
2. 精确计算：使用模糊音权重 + 边界约束进行精确匹配
3. 音形义联合排序：使用字形相似度优化候选排序

可选：FAISS 向量索引加速（大规模热词场景）
"""
import os
import logging
import threading
from typing import List, Dict, Tuple, NamedTuple, Optional

import numpy as np

from src.core.hotword.phoneme import Phoneme, get_phoneme_info, SIMILAR_PHONEMES
from src.core.hotword.rag import FastRAG
from src.core.hotword.algo_calc import fuzzy_substring_search_constrained
from src.core.hotword.variants import (
    generate_hotword_variants,
    nfkc_normalize,
    parse_hotword_line,
)

logger = logging.getLogger(__name__)


class MatchResult(NamedTuple):
    """匹配结果"""
    start: int      # 字符起始位置
    end: int        # 字符结束位置
    score: float    # 匹配分数
    hotword: str    # 热词
    original: str   # 原始文本片段


class CorrectionResult(NamedTuple):
    """纠错结果"""
    text: str                                   # 纠错后的文本
    matches: List[Tuple[str, str, float]]       # [(原词, 热词, 分数), ...]
    similars: List[Tuple[str, str, float]]      # [(原词, 热词, 分数), ...]


class PhonemeCorrector:
    """
    基于音素的热词纠错器 - 两阶段检索

    使用两阶段检索策略：
    1. FastRAG 粗筛：使用倒排索引快速过滤候选
    2. 精确计算：使用 fuzzy_substring_search_constrained 进行边界约束匹配

    可选 FAISS 向量索引：
    - 大规模热词场景（>1000）性能提升 10x
    - 支持 IVF/HNSW 索引类型

    优势：
    - FastRAG 倒排索引减少 90% 计算量
    - 边界约束搜索保证只在词边界匹配
    - 模糊音权重提高相似音匹配精度
    """

    def __init__(
        self,
        threshold: float = 0.8,
        similar_threshold: float = None,
        cache_size: int = 1000,
        use_faiss: bool = False,
        faiss_index_type: str = "IVFFlat",
        use_shape_rerank: bool = True,
        shape_weight: float = 0.15,
    ):
        """
        初始化纠错器

        Args:
            threshold: 替换阈值 (高于此分数才替换)
            similar_threshold: 相似度阈值 (高于此分数加入相似列表)
            cache_size: 缓存大小 (最大缓存条目数)
            use_faiss: 是否使用 FAISS 向量索引
            faiss_index_type: FAISS 索引类型 (IVFFlat, HNSW)
            use_shape_rerank: 是否使用字形相似度重排序
            shape_weight: 字形相似度权重 (用于联合评分)
        """
        self.threshold = threshold
        self.similar_threshold = similar_threshold if similar_threshold is not None else (threshold - 0.2)
        self.hotwords: Dict[str, List[Phoneme]] = {}
        self.fast_rag = FastRAG(threshold=min(self.threshold, self.similar_threshold) - 0.1)
        self._lock = threading.Lock()
        self._cache: Dict[str, CorrectionResult] = {}
        self._cache_size = cache_size

        # 字形联合纠错
        self.use_shape_rerank = use_shape_rerank
        self.shape_weight = shape_weight
        self._shape_corrector = None

        # FAISS 相关
        self.use_faiss = use_faiss
        self.faiss_index_type = faiss_index_type
        self._faiss_index = None
        self._faiss_hotword_keys: List[str] = []
        self._faiss_available = False

        if use_faiss:
            self._init_faiss()

        # Hotword canonical/alias mapping (enterprise mode):
        # - self.hotwords keys are *variants* used for matching (canonical + aliases + auto variants)
        # - replacement output must always use canonical
        self._variant_to_canonical: Dict[str, str] = {}

    @property
    def shape_corrector(self):
        """懒加载字形纠错器"""
        if self._shape_corrector is None and self.use_shape_rerank:
            try:
                from src.core.hotword.shape_corrector import ShapeCorrector
                self._shape_corrector = ShapeCorrector()
            except Exception as e:
                logger.warning(f"Shape corrector unavailable: {e}")
                self.use_shape_rerank = False
        return self._shape_corrector

    def _init_faiss(self):
        """初始化 FAISS"""
        try:
            import faiss
            self._faiss_available = True
            logger.info("FAISS initialized successfully")
        except ImportError:
            logger.warning("FAISS not installed. Install with: pip install faiss-cpu (or faiss-gpu)")
            self._faiss_available = False
            self.use_faiss = False

    def _phoneme_to_vector(self, phonemes: List[Phoneme], dim: int = 128) -> np.ndarray:
        """将音素序列转换为向量表示

        简单的向量化：使用音素 hash 的特征
        """
        vec = np.zeros(dim, dtype=np.float32)

        for i, ph in enumerate(phonemes):
            # 位置加权
            weight = 1.0 / (1 + i * 0.1)

            # 音素特征
            info = ph.info
            # info = (value, lang, word_start, word_end, char_start, char_end)
            phoneme_hash = hash(info[0]) % dim
            vec[phoneme_hash] += weight

            # 语言特征
            lang_hash = hash(info[1]) % dim
            vec[lang_hash] += weight * 0.5

        # L2 归一化
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return vec

    def _build_faiss_index(self):
        """构建 FAISS 索引"""
        if not self._faiss_available or not self.hotwords:
            return

        import faiss

        dim = 128
        hotword_keys = list(self.hotwords.keys())
        vectors = np.zeros((len(hotword_keys), dim), dtype=np.float32)

        for i, hw in enumerate(hotword_keys):
            vectors[i] = self._phoneme_to_vector(self.hotwords[hw], dim)

        # 根据热词数量选择索引类型
        n_hotwords = len(hotword_keys)

        if self.faiss_index_type == "HNSW" and n_hotwords >= 100:
            # HNSW 索引 - 适合中大规模
            index = faiss.IndexHNSWFlat(dim, 32)
            index.hnsw.efConstruction = 40
            index.add(vectors)
        elif self.faiss_index_type == "IVFFlat" and n_hotwords >= 1000:
            # IVF 索引 - 适合大规模
            nlist = min(100, n_hotwords // 10)
            quantizer = faiss.IndexFlatL2(dim)
            index = faiss.IndexIVFFlat(quantizer, dim, nlist)
            index.train(vectors)
            index.add(vectors)
            index.nprobe = 10
        else:
            # 小规模直接使用 Flat 索引
            index = faiss.IndexFlatL2(dim)
            index.add(vectors)

        self._faiss_index = index
        self._faiss_hotword_keys = hotword_keys
        logger.info(f"FAISS index built: type={type(index).__name__}, n_hotwords={n_hotwords}")

    def _faiss_search(self, input_phonemes: List[Phoneme], top_k: int = 100) -> List[Tuple[str, float]]:
        """使用 FAISS 搜索候选热词"""
        if not self._faiss_available or self._faiss_index is None:
            return []

        query_vec = self._phoneme_to_vector(input_phonemes).reshape(1, -1)
        distances, indices = self._faiss_index.search(query_vec, min(top_k, len(self._faiss_hotword_keys)))

        results = []
        for i, idx in enumerate(indices[0]):
            if idx >= 0 and idx < len(self._faiss_hotword_keys):
                hw = self._faiss_hotword_keys[idx]
                # 将 L2 距离转换为相似度分数 (0-1)
                score = 1.0 / (1.0 + distances[0][i])
                results.append((hw, score))

        return results

    def update_hotwords(self, text: str) -> int:
        """从文本更新热词。

        Supports optional alias syntax per line:
            canonical | alias1 | alias2

        The matcher will include canonical + aliases + auto-generated variants,
        but replacements always emit the canonical form.

        Returns:
            canonical hotword count (stable for API/UI), not total variants.
        """
        raw_lines = [
            l.strip()
            for l in str(text or "").splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]

        new_hotwords: Dict[str, List[Phoneme]] = {}
        variant_to_canonical: Dict[str, str] = {}
        canonicals: List[str] = []

        for raw in raw_lines:
            canonical, aliases = parse_hotword_line(raw)
            canonical = str(canonical or "").strip()
            if not canonical:
                continue

            canonicals.append(canonical)

            variants = set(generate_hotword_variants(canonical))

            # Explicit aliases (also include their NFKC normalized forms to be tolerant).
            for a in aliases:
                a0 = str(a or "").strip()
                if not a0:
                    continue
                variants.add(a0)
                a_nfkc = nfkc_normalize(a0).strip()
                if a_nfkc:
                    variants.add(a_nfkc)

            # Ensure canonical itself is always present (even if max_variants truncated).
            variants.add(canonical)

            for v in variants:
                vv = str(v or "").strip()
                if not vv:
                    continue
                phs = get_phoneme_info(vv)
                if not phs:
                    continue
                new_hotwords[vv] = phs
                variant_to_canonical[vv] = canonical

        with self._lock:
            self.hotwords = new_hotwords
            self._variant_to_canonical = variant_to_canonical
            self.fast_rag = FastRAG(threshold=min(self.threshold, self.similar_threshold) - 0.1)
            self.fast_rag.add_hotwords(new_hotwords)
            self._cache.clear()  # 热词变化时清空缓存

            # 重建 FAISS 索引
            if self.use_faiss and self._faiss_available:
                self._build_faiss_index()

        # Return canonical count (stable across variant generation).
        return len(set(canonicals))

    def load_hotwords_file(self, path: str) -> int:
        """从文件加载热词"""
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return self.update_hotwords(f.read())
        return 0

    def correct(self, text: str, top_k: int = 10) -> CorrectionResult:
        """
        执行热词纠错

        Args:
            text: 输入文本
            top_k: 返回相似热词的最大数量

        Returns:
            CorrectionResult(纠错后文本, 匹配列表, 相似列表)
        """
        if not text or not self.hotwords:
            return CorrectionResult(text or "", [], [])

        # 缓存检查
        if text in self._cache:
            return self._cache[text]

        input_phs = get_phoneme_info(text)
        if not input_phs:
            return CorrectionResult(text, [], [])

        with self._lock:
            # 阶段1: 粗筛
            if self.use_faiss and self._faiss_available and self._faiss_index is not None:
                # 使用 FAISS 向量检索
                faiss_results = self._faiss_search(input_phs, top_k=100)
                # 合并 FastRAG 结果
                fast_results = self.fast_rag.search(input_phs, top_k=50)
                # 合并去重
                seen = set()
                combined_results = []
                for hw, score in faiss_results + fast_results:
                    if hw not in seen:
                        combined_results.append((hw, score))
                        seen.add(hw)
                fast_results = combined_results[:100]
            else:
                # 仅使用 FastRAG
                fast_results = self.fast_rag.search(input_phs, top_k=100)

            # 阶段2: 精确匹配 (带边界约束)
            input_processed = [p.info for p in input_phs]
            matches, similars = self._find_matches(text, fast_results, input_processed)

        # 阶段3: 冲突解决与替换
        new_text, final_matches = self._resolve_and_replace(text, matches)

        result = CorrectionResult(
            new_text,
            final_matches,
            similars[:top_k]
        )

        # 写入缓存
        if len(self._cache) < self._cache_size:
            self._cache[text] = result

        return result

    def _find_matches(
        self,
        text: str,
        fast_results: List[Tuple[str, float]],
        input_processed: List[Tuple]
    ) -> Tuple[List[MatchResult], List[Tuple[str, str, float]]]:
        """
        精细匹配逻辑：边界约束的模糊搜索

        Args:
            text: 原始文本
            fast_results: FastRAG 粗筛结果 [(热词, 分数), ...]
            input_processed: 输入音素 info 元组列表

        Returns:
            (matches, similars)
        """
        matches = []
        similars = []
        search_threshold = min(self.threshold, self.similar_threshold) - 0.1

        for hw, fast_score in fast_results:
            hw_phonemes = self.hotwords[hw]
            hw_compare = [p.info for p in hw_phonemes]
            canonical = self._variant_to_canonical.get(hw, hw)

            # 使用边界约束搜索
            found_segments = fuzzy_substring_search_constrained(
                hw_compare, input_processed, threshold=search_threshold
            )

            for score, start_phon_idx, end_phon_idx in found_segments:
                # 从 input_processed 获取字符位置
                char_start = input_processed[start_phon_idx][5]
                char_end = input_processed[end_phon_idx - 1][6]
                original = text[char_start:char_end]

                # 音形义联合评分：将字形相似度融入最终分数
                if self.use_shape_rerank and self.shape_corrector and original != hw:
                    # IMPORTANT: shape similarity should be computed against the matched variant
                    # (not canonical) to avoid penalizing "spoken numeric" aliases (二点零 -> 2.0).
                    shape_sim = self.shape_corrector.text_shape_similarity(original, hw)
                    # 联合分数: (1-w)*phoneme + w*shape
                    score = (1 - self.shape_weight) * score + self.shape_weight * shape_sim

                res = MatchResult(char_start, char_end, score, canonical, original)

                # 分类到 matches 和 similars
                if score >= self.threshold:
                    matches.append(res)

                if score >= self.similar_threshold:
                    similars.append((original, canonical, round(score, 3)))

        # 相似列表去重与排序
        seen_hw = set()
        final_similars = []
        similars.sort(key=lambda x: (x[2], len(x[1])), reverse=True)

        for original, hw, score in similars:
            if hw not in seen_hw:
                final_similars.append((original, hw, score))
                seen_hw.add(hw)

        return matches, final_similars

    def _resolve_and_replace(
        self,
        text: str,
        matches: List[MatchResult]
    ) -> Tuple[str, List[Tuple[str, str, float]]]:
        """
        冲突解决与文本替换

        Args:
            text: 原始文本
            matches: 匹配结果列表

        Returns:
            (替换后文本, [(原词, 热词, 分数), ...])
        """
        # 分数优先 > 长度优先
        matches.sort(key=lambda x: (x.score, x.end - x.start), reverse=True)

        final_matches = []
        occupied_ranges = []

        for m in matches:
            if m.score < self.threshold:
                continue

            # 检查重叠
            is_overlap = False
            for r_start, r_end in occupied_ranges:
                if not (m.end <= r_start or m.start >= r_end):
                    is_overlap = True
                    break

            if not is_overlap:
                # 检查是否真的有变化
                if text[m.start:m.end] != m.hotword:
                    final_matches.append(m)
                occupied_ranges.append((m.start, m.end))

        # 执行替换 (从后向前，避免索引偏移)
        final_matches.sort(key=lambda x: x.start, reverse=True)
        result_list = list(text)
        for m in final_matches:
            result_list[m.start:m.end] = list(m.hotword)

        # 构建返回结果
        result_info = [
            (m.original, m.hotword, round(m.score, 3))
            for m in sorted(final_matches, key=lambda x: x.start)
        ]

        return "".join(result_list), result_info
