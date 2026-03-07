"""
智能音频分块模块
基于 VAD 的智能分割，支持分块重叠处理和并行转写。
"""

import logging
import numpy as np
from typing import List, Tuple, Optional, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio

from src.core.text_processor.text_merge import merge_by_text
from src.core.text_processor.text_merge_accu import (
    chars_to_text,
    linear_chars_with_timestamps,
    merge_chars_by_sequence_matcher,
)

logger = logging.getLogger(__name__)


class AudioChunker:
    """智能音频分块器

    功能:
    - 基于 VAD 的智能分割点检测
    - 分块重叠避免边界截断
    - 支持并行处理分块
    - 结果合并

    用法:
        chunker = AudioChunker(max_chunk_duration=60.0)
        chunks = chunker.split(audio, sample_rate)
        results = chunker.process_parallel(chunks, transcribe_func)
        final_text = chunker.merge_results(results)
    """

    def __init__(
        self,
        max_chunk_duration: float = 60.0,
        min_chunk_duration: float = 5.0,
        overlap_duration: float = 0.5,
        silence_threshold_db: float = -40.0,
        min_silence_duration: float = 0.3,
        strategy: str = "silence",
    ):
        """
        初始化分块器

        Args:
            max_chunk_duration: 最大分块时长(秒)
            min_chunk_duration: 最小分块时长(秒)
            overlap_duration: 分块重叠时长(秒)
            silence_threshold_db: 静音检测阈值(dB)
            min_silence_duration: 最小静音时长(秒)，用于检测分割点
            strategy: 分块策略:
                - "silence": 优先选择静音分割点 (默认)
                - "time": 固定时长切片 (CapsWriter-style，不依赖静音/VAD)
        """
        self.max_chunk_duration = max_chunk_duration
        self.min_chunk_duration = min_chunk_duration
        self.overlap_duration = overlap_duration
        self.silence_threshold = 10 ** (silence_threshold_db / 20)
        self.min_silence_duration = min_silence_duration
        self.strategy = strategy

    def _split_by_time(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> List[Tuple[np.ndarray, int, int]]:
        """固定时长切片 + overlap (不依赖静音检测)."""
        duration = len(audio) / sample_rate
        if duration <= self.max_chunk_duration:
            return [(audio, 0, len(audio))]

        max_samples = int(self.max_chunk_duration * sample_rate)
        min_samples = int(self.min_chunk_duration * sample_rate)
        overlap_samples = int(self.overlap_duration * sample_rate)

        chunks: List[Tuple[np.ndarray, int, int]] = []
        current_start = 0

        while current_start < len(audio):
            target_end = min(current_start + max_samples, len(audio))

            if target_end >= len(audio):
                chunks.append((audio[current_start:], current_start, len(audio)))
                break

            best_split = target_end
            chunk_end = min(best_split + overlap_samples, len(audio))
            chunks.append((audio[current_start:chunk_end], current_start, chunk_end))

            next_start = max(best_split - overlap_samples, current_start + min_samples)
            if next_start <= current_start:
                # Guard against stalling (e.g. weird min/overlap settings).
                next_start = min(current_start + max(1, min_samples), len(audio))
            current_start = next_start

        return chunks

    def _find_silence_candidates(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> List[Tuple[int, int]]:
        """查找静音候选点 (mid_sample, silence_length_frames).

        Note:
            We keep a richer representation than `_find_silence_points` so the splitter
            can score candidates (prefer longer continuous silence, then closer to target end).
        """
        # 帧参数
        frame_length = int(sample_rate * 0.025)  # 25ms
        hop_length = int(sample_rate * 0.010)    # 10ms
        min_silence_frames = int(self.min_silence_duration / 0.010)

        # 计算帧能量
        num_frames = (len(audio) - frame_length) // hop_length + 1
        if num_frames <= 0:
            return []

        frame_energies = np.zeros(num_frames)
        for i in range(num_frames):
            start = i * hop_length
            end = start + frame_length
            frame = audio[start:end]
            frame_energies[i] = np.sqrt(np.mean(frame ** 2))

        # 找到静音区域
        is_silence = frame_energies < self.silence_threshold

        candidates: List[Tuple[int, int]] = []
        silence_start = None

        for i, silent in enumerate(is_silence):
            if silent:
                if silence_start is None:
                    silence_start = i
            else:
                if silence_start is not None:
                    silence_length = i - silence_start
                    if silence_length >= min_silence_frames:
                        mid_frame = silence_start + silence_length // 2
                        mid_sample = mid_frame * hop_length
                        candidates.append((mid_sample, silence_length))
                    silence_start = None

        return candidates

    def _find_silence_points(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> List[int]:
        """查找静音分割点

        Args:
            audio: 音频数据
            sample_rate: 采样率

        Returns:
            静音点位置列表 (样本索引)
        """
        candidates = self._find_silence_candidates(audio, sample_rate=sample_rate)
        return [mid_sample for mid_sample, _silence_length in candidates]

    def split(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> List[Tuple[np.ndarray, int, int]]:
        """分割长音频为多个块

        Args:
            audio: 音频数据
            sample_rate: 采样率

        Returns:
            分块列表 [(chunk_audio, start_sample, end_sample), ...]
        """
        duration = len(audio) / sample_rate

        # 如果音频较短，不需要分割
        if duration <= self.max_chunk_duration:
            return [(audio, 0, len(audio))]

        strategy = (getattr(self, "strategy", None) or "silence").strip().lower()
        if strategy == "time":
            chunks = self._split_by_time(audio, sample_rate=sample_rate)
            logger.info(f"Split audio ({duration:.1f}s) into {len(chunks)} chunks (strategy=time)")
            return chunks

        # 找到所有静音候选点 (mid_sample, silence_length_frames)
        silence_candidates = self._find_silence_candidates(audio, sample_rate)

        # 计算分块边界
        max_samples = int(self.max_chunk_duration * sample_rate)
        min_samples = int(self.min_chunk_duration * sample_rate)
        overlap_samples = int(self.overlap_duration * sample_rate)

        chunks = []
        current_start = 0

        while current_start < len(audio):
            # 目标结束位置
            target_end = min(current_start + max_samples, len(audio))

            if target_end >= len(audio):
                # 最后一个块
                chunks.append((audio[current_start:], current_start, len(audio)))
                break

            # 在目标结束位置附近查找最佳分割点
            best_split = target_end
            search_start = max(current_start + min_samples, target_end - max_samples // 4)

            # Score candidates within the search range:
            # - Prefer longer continuous silence (lower mid-word cut risk)
            # - Then prefer candidates closer to target_end (fewer boundaries overall)
            best_key = None
            for mid_sample, silence_length_frames in silence_candidates:
                if mid_sample < search_start:
                    continue
                if mid_sample > target_end:
                    break

                key = (silence_length_frames, mid_sample)
                if best_key is None or key > best_key:
                    best_key = key
                    best_split = mid_sample

            # 添加分块 (包含重叠)
            chunk_end = min(best_split + overlap_samples, len(audio))
            chunks.append((audio[current_start:chunk_end], current_start, chunk_end))

            # 下一个块的起始位置 (减去重叠)
            current_start = max(best_split - overlap_samples, current_start + min_samples)

        logger.info(f"Split audio ({duration:.1f}s) into {len(chunks)} chunks (strategy=silence)")
        return chunks

    def process_parallel(
        self,
        chunks: List[Tuple[np.ndarray, int, int]],
        transcribe_func,
        max_workers: int = 2,
        on_progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """并行处理分块

        Args:
            chunks: 分块列表
            transcribe_func: 转写函数，接受音频数据返回结果字典
            max_workers: 最大工作线程数

        Returns:
            各分块的转写结果
        """
        def process_chunk(args):
            chunk_audio, start_sample, end_sample = args
            try:
                result = transcribe_func(chunk_audio)
                return {
                    "start_sample": start_sample,
                    "end_sample": end_sample,
                    "success": True,
                    "result": result,
                }
            except Exception as e:
                logger.error(f"Chunk processing failed: {e}")
                return {
                    "start_sample": start_sample,
                    "end_sample": end_sample,
                    "success": False,
                    "error": str(e),
                }

        total = len(chunks)
        results: List[Optional[Dict[str, Any]]] = [None] * total
        done = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_chunk, chunk): idx
                for idx, chunk in enumerate(chunks)
            }

            for future in as_completed(futures):
                idx = futures[future]
                res = future.result()
                results[idx] = res
                done += 1
                if on_progress is not None:
                    try:
                        on_progress(done, total, res)
                    except Exception as e:
                        logger.debug(f"Chunk progress callback failed (ignored): {e}")

        # All futures completed; `results` should be fully populated.
        return [r for r in results if r is not None]

    async def process_parallel_async(
        self,
        chunks: List[Tuple[np.ndarray, int, int]],
        transcribe_func,
        max_concurrent: int = 2,
    ) -> List[Dict[str, Any]]:
        """异步并行处理分块

        Args:
            chunks: 分块列表
            transcribe_func: 异步转写函数
            max_concurrent: 最大并发数

        Returns:
            各分块的转写结果
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_chunk(chunk_audio, start_sample, end_sample):
            async with semaphore:
                try:
                    result = await transcribe_func(chunk_audio)
                    return {
                        "start_sample": start_sample,
                        "end_sample": end_sample,
                        "success": True,
                        "result": result,
                    }
                except Exception as e:
                    logger.error(f"Chunk processing failed: {e}")
                    return {
                        "start_sample": start_sample,
                        "end_sample": end_sample,
                        "success": False,
                        "error": str(e),
                    }

        tasks = [
            process_chunk(chunk_audio, start, end)
            for chunk_audio, start, end in chunks
        ]
        return await asyncio.gather(*tasks)

    def merge_results(
        self,
        chunk_results: List[Dict[str, Any]],
        sample_rate: int = 16000,
        overlap_chars: int = 10,
        return_accu_timestamps: bool = False,
    ) -> Dict[str, Any]:
        """合并分块转写结果

        Args:
            chunk_results: 分块结果列表
            sample_rate: 采样率
            overlap_chars: 重叠文本字符数 (用于去重)
            return_accu_timestamps: 是否返回 text_accu 的字符级时间戳（用于对齐/检索）

        Returns:
            合并后的结果
        """
        # 按起始位置排序
        sorted_results = sorted(chunk_results, key=lambda x: x["start_sample"])

        merged_text = ""
        merged_accu_chars: List[str] = []
        merged_accu_ts: List[float] = []
        all_sentences = []
        prev_end_sample: Optional[int] = None

        for result in sorted_results:
            if not result["success"]:
                continue

            chunk_result = result.get("result", {})
            text = chunk_result.get("text", "")
            sentences = chunk_result.get("sentences", [])

            start_sample = int(result.get("start_sample", 0) or 0)
            end_sample = int(result.get("end_sample", 0) or 0)

            # 时间偏移
            time_offset_ms = int(start_sample / sample_rate * 1000)

            # Overlap window (ms) between previous successful chunk and current chunk.
            overlap_ms = 0
            overlap_s = 0.0
            if prev_end_sample is not None and prev_end_sample > start_sample:
                overlap_samples = prev_end_sample - start_sample
                overlap_ms = int(overlap_samples / sample_rate * 1000)
                overlap_s = float(overlap_samples) / float(sample_rate)

            # Snapshot of already-merged text before integrating this chunk.
            prev_text_snapshot = merged_text
            tail_len = max(200, int(overlap_chars) * 4) if overlap_chars > 0 else 200
            prev_tail = prev_text_snapshot[-tail_len:]
            overlap_end_ms = time_offset_ms + overlap_ms + 50  # small tolerance for rounding/jitter

            # 调整句子时间戳
            for sent in sentences:
                sent_text = sent.get("text", "")
                sent["start"] = sent.get("start", 0) + time_offset_ms
                sent["end"] = sent.get("end", 0) + time_offset_ms

                # Dedupe sentences that fall completely inside the overlap region and
                # are already present in the previously merged text tail.
                if overlap_ms > 0 and sent_text and sent["end"] <= overlap_end_ms:
                    if sent_text in prev_tail:
                        continue

                all_sentences.append(sent)

            if text:
                merged_text = merge_by_text(
                    merged_text,
                    text,
                    overlap_chars=max(0, int(overlap_chars)),
                )

                # Precision merge (text_accu): time-windowed SequenceMatcher alignment.
                # We approximate timestamps per-character over the chunk time range.
                start_s = float(start_sample) / float(sample_rate)
                end_s = float(end_sample) / float(sample_rate) if end_sample > 0 else start_s
                new_chars, new_ts = linear_chars_with_timestamps(text, start_s=start_s, end_s=end_s)
                merged_accu_chars, merged_accu_ts = merge_chars_by_sequence_matcher(
                    merged_accu_chars,
                    merged_accu_ts,
                    new_chars,
                    new_ts,
                    offset_s=start_s,
                    overlap_s=overlap_s,
                    is_first_segment=not merged_accu_chars,
                )

            if end_sample > 0:
                prev_end_sample = end_sample if prev_end_sample is None else max(prev_end_sample, end_sample)

        return {
            "text": merged_text,
            "text_accu": chars_to_text(merged_accu_chars) if merged_accu_chars else merged_text,
            "sentences": all_sentences,
            **(
                {"accu_chars": merged_accu_chars, "accu_ts": merged_accu_ts}
                if bool(return_accu_timestamps)
                else {}
            ),
        }


# 全局分块器实例
audio_chunker = AudioChunker()
