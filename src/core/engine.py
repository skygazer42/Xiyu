"""核心转写引擎"""
import asyncio
import logging
import time
from typing import Dict, Any, Optional, Union, List, Tuple, Callable
from pathlib import Path
import json

import numpy as np
import httpx

from src.config import settings
from src.models.model_manager import model_manager
from src.core.hotword import PhonemeCorrector
from src.core.hotword.rule_corrector import RuleCorrector
from src.core.hotword.rectification import RectificationRAG
from src.core.hotword.variants import parse_hotword_line
from src.core.speaker import SpeakerLabeler, build_speaker_turns
from src.core.llm import LLMClient, LLMMessage, PromptBuilder
from src.core.llm.roles import get_role
from src.core.text_processor import TextPostProcessor, PostProcessorSettings
from src.core.text_processor.text_corrector import TextCorrector
from src.core.audio.chunker import AudioChunker
from src.core.audio.slice import ensure_pcm16le_16k_mono_bytes, slice_pcm16le
from src.models.backends.remote_utils import pcm16le_to_wav_bytes
from src.core.speaker.external_diarizer_client import fetch_diarizer_segments
from src.core.speaker.external_diarizer_normalize import normalize_segments
from src.core.speaker.external_diarizer_turns import segments_to_turns
from src.utils.service_metrics import metrics

logger = logging.getLogger(__name__)


class TranscriptionEngine:
    """转写引擎 - 整合 ASR + 热词纠错 + 说话人识别 + LLM润色"""

    def __init__(self):
        self.corrector = PhonemeCorrector(
            threshold=settings.hotwords_threshold,
            similar_threshold=settings.hotwords_threshold - 0.2,
            use_faiss=settings.hotword_use_faiss,
            faiss_index_type=settings.hotword_faiss_index_type,
        )
        self.rule_corrector = RuleCorrector()
        self.rectification_rag = RectificationRAG()
        self.speaker_labeler = SpeakerLabeler(label_style=settings.speaker_label_style)

        # 文本后处理器
        self.post_processor = TextPostProcessor.from_config(settings)

        # 长音频分块器
        self.audio_chunker = AudioChunker(
            max_chunk_duration=settings.vad_max_segment_ms / 1000.0,
            min_chunk_duration=5.0,
            overlap_duration=0.5,
        )

        # 通用文本纠错器 (pycorrector)
        self._text_corrector: Optional[TextCorrector] = None
        self._text_correct_enabled = settings.text_correct_enable

        # LLM 组件
        self._llm_client: Optional[LLMClient] = None
        self._prompt_builder: Optional[PromptBuilder] = None
        # Forced hotwords (强制替换/纠错)：用于 PhonemeCorrector + rules 等纠错链路。
        self._hotwords_list: List[str] = []
        # Context hotwords (上下文提示)：仅用于前向注入/提示模型，不做强制替换。
        self._context_hotwords_list: List[str] = []

        self._hotwords_loaded = False
        self._context_hotwords_loaded = False
        self._rules_loaded = False
        self._rectify_loaded = False

    def warmup(self, duration: float = 1.0) -> Dict[str, Any]:
        """预热模型，消除首次推理延迟

        Args:
            duration: 预热音频时长(秒)

        Returns:
            预热结果统计
        """
        import numpy as np

        results = {
            "backend": model_manager.backend.get_info()["name"],
            "warmup_duration": duration,
            "timings": {}
        }

        # 生成静默音频
        sample_rate = 16000
        samples = int(sample_rate * duration)
        silent_audio = np.zeros(samples, dtype=np.float32)

        logger.info(f"Warming up transcription engine ({duration}s audio)...")

        # 预热 ASR 后端
        backend = model_manager.backend
        start_time = time.time()

        try:
            # 尝试调用后端的 warmup 方法
            if hasattr(backend, 'warmup'):
                backend.warmup(duration)
            else:
                # 直接进行一次推理
                _ = backend.transcribe(silent_audio)

            results["timings"]["asr"] = time.time() - start_time
            logger.info(f"ASR warmup completed in {results['timings']['asr']:.2f}s")
        except Exception as e:
            logger.warning(f"ASR warmup failed: {e}")
            results["timings"]["asr"] = -1

        # 预热热词纠错器
        start_time = time.time()
        try:
            if self._hotwords_loaded:
                _ = self.corrector.correct("测试预热文本")
                results["timings"]["hotword"] = time.time() - start_time
        except Exception as e:
            logger.warning(f"Hotword warmup failed: {e}")

        logger.info("Engine warmup completed")
        return results

    @property
    def llm_client(self) -> LLMClient:
        """懒加载 LLM 客户端"""
        if self._llm_client is None:
            self._llm_client = LLMClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                backend=settings.llm_backend,
                max_tokens=settings.llm_max_tokens,
                cache_enable=settings.llm_cache_enable,
                cache_size=settings.llm_cache_size,
                cache_ttl=settings.llm_cache_ttl,
            )
        return self._llm_client

    @property
    def text_corrector(self) -> Optional[TextCorrector]:
        """懒加载文本纠错器"""
        if self._text_corrector is None and self._text_correct_enabled:
            try:
                self._text_corrector = TextCorrector(
                    backend=settings.text_correct_backend,
                    device=settings.text_correct_device,
                )
            except Exception as e:
                logger.error(f"Failed to initialize TextCorrector: {e}")
                self._text_correct_enabled = False
        return self._text_corrector

    def load_hotwords(self, path: Optional[str] = None):
        """加载热词"""
        if path is None:
            path = str(settings.hotwords_dir / settings.hotwords_file)

        if Path(path).exists():
            count = self.corrector.load_hotwords_file(path)
            # 缓存热词列表供 LLM 使用
            with open(path, 'r', encoding='utf-8') as f:
                # Only keep canonical terms (strip optional alias syntax `a|b|c`).
                canonical_list: List[str] = []
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    canonical, _aliases = parse_hotword_line(s)
                    canonical = str(canonical or "").strip()
                    if canonical:
                        canonical_list.append(canonical)
                self._hotwords_list = canonical_list
            logger.info(f"Loaded {count} hotwords from {path}")
            self._hotwords_loaded = True
        else:
            logger.warning(f"Hotwords file not found: {path}")

    def load_context_hotwords(self, path: Optional[str] = None) -> None:
        """加载上下文热词（仅用于注入提示，不强制替换）"""
        if path is None:
            path = str(settings.hotwords_dir / settings.hotwords_context_file)

        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                canonical_list: List[str] = []
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    canonical, _aliases = parse_hotword_line(s)
                    canonical = str(canonical or "").strip()
                    if canonical:
                        canonical_list.append(canonical)
                self._context_hotwords_list = canonical_list
            logger.info(f"Loaded {len(self._context_hotwords_list)} context hotwords from {path}")
            self._context_hotwords_loaded = True
        else:
            logger.warning(f"Context hotwords file not found: {path}")

    def load_rules(self, path: Optional[str] = None):
        """加载规则"""
        if path is None:
            path = str(settings.hotwords_dir / "hot-rules.txt")

        if Path(path).exists():
            count = self.rule_corrector.load_rules_file(path)
            logger.info(f"Loaded {count} rules from {path}")
            self._rules_loaded = True
        else:
            logger.warning(f"Rules file not found: {path}")

    def load_rectify_history(self, path: Optional[str] = None):
        """加载纠错历史"""
        if path is None:
            path = str(settings.hotwords_dir / "hot-rectify.txt")

        if Path(path).exists():
            self.rectification_rag = RectificationRAG(rectify_file=path)
            count = self.rectification_rag.load_history()
            logger.info(f"Loaded {count} rectify records from {path}")
            self._rectify_loaded = True
        else:
            logger.warning(f"Rectify file not found: {path}")

    def load_all(self):
        """加载所有热词相关文件"""
        self.load_hotwords()
        self.load_context_hotwords()
        self.load_rules()
        self.load_rectify_history()

    def update_hotwords(self, hotwords: Union[str, List[str]]):
        """更新热词"""
        if isinstance(hotwords, list):
            # Treat provided items as canonical terms.
            canonical_terms = [str(s).strip() for s in hotwords if str(s).strip()]
            self._hotwords_list = canonical_terms
            hotwords = "\n".join(hotwords)
        else:
            canonical_terms: List[str] = []
            for line in str(hotwords).split("\n"):
                s = str(line or "").strip()
                if not s or s.startswith("#"):
                    continue
                canonical, _aliases = parse_hotword_line(s)
                canonical = str(canonical or "").strip()
                if canonical:
                    canonical_terms.append(canonical)
            self._hotwords_list = canonical_terms

        count = self.corrector.update_hotwords(hotwords)
        logger.info(f"Updated {count} hotwords")
        self._hotwords_loaded = True

    def update_context_hotwords(self, hotwords: Union[str, List[str]]) -> None:
        """更新上下文热词（仅用于注入提示，不强制替换）"""
        if isinstance(hotwords, list):
            self._context_hotwords_list = [str(s).strip() for s in hotwords if str(s).strip()]
        else:
            canonical_terms: List[str] = []
            for line in str(hotwords).split("\n"):
                s = str(line or "").strip()
                if not s or s.startswith("#"):
                    continue
                canonical, _aliases = parse_hotword_line(s)
                canonical = str(canonical or "").strip()
                if canonical:
                    canonical_terms.append(canonical)
            self._context_hotwords_list = canonical_terms

        logger.info(f"Updated {len(self._context_hotwords_list)} context hotwords")
        self._context_hotwords_loaded = True

    def _get_injection_hotwords(self, custom_hotwords: Optional[str] = None) -> Optional[str]:
        """获取用于前向注入的热词字符串

        Args:
            custom_hotwords: 自定义热词（优先使用）

        Returns:
            热词字符串（换行分隔）或 None
        """
        # 如果提供了自定义热词，直接使用
        if custom_hotwords:
            return custom_hotwords

        # 检查是否启用前向注入
        if not settings.hotword_injection_enable:
            return None

        # 检查是否有已加载的热词
        if not self._context_hotwords_list and not self._hotwords_list:
            return None

        # 截取最大数量并拼接
        max_count = settings.hotword_injection_max
        # Prefer context hotwords for injection (更安全), but also include the
        # forced list as extra hints (injection is not replacement), to improve
        # proper-noun recall in meetings.
        merged: List[str] = []
        seen = set()

        def _add(hw: str) -> None:
            s = str(hw).strip()
            if not s:
                return
            if s in seen:
                return
            seen.add(s)
            merged.append(s)

        for hw in self._context_hotwords_list:
            if len(merged) >= max_count:
                break
            _add(hw)

        for hw in self._hotwords_list:
            if len(merged) >= max_count:
                break
            _add(hw)

        if not merged:
            return None
        return "\n".join(merged)

    def _get_request_post_processor(self, asr_options: Optional[Dict[str, Any]]) -> TextPostProcessor:
        """Build a request-scoped post-processor (does not mutate global settings)."""
        postprocess_options = None
        if isinstance(asr_options, dict):
            postprocess_options = asr_options.get("postprocess")

        if not isinstance(postprocess_options, dict) or not postprocess_options:
            return self.post_processor

        # Base from current Settings, then override known keys.
        pp_settings = PostProcessorSettings(
            filler_remove_enable=settings.filler_remove_enable,
            filler_aggressive=settings.filler_aggressive,
            qj2bj_enable=settings.qj2bj_enable,
            itn_enable=settings.itn_enable,
            itn_erhua_remove=settings.itn_erhua_remove,
            spacing_cjk_ascii_enable=settings.spacing_cjk_ascii_enable,
            spoken_punc_enable=settings.spoken_punc_enable,
            acronym_merge_enable=settings.acronym_merge_enable,
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
        for k, v in postprocess_options.items():
            if hasattr(pp_settings, k):
                setattr(pp_settings, k, v)

        return TextPostProcessor(pp_settings)

    def _get_request_chunker(self, asr_options: Optional[Dict[str, Any]]) -> AudioChunker:
        """Build a request-scoped AudioChunker based on `asr_options.chunking`."""
        import math

        chunking_options = None
        if isinstance(asr_options, dict):
            chunking_options = asr_options.get("chunking")

        if not isinstance(chunking_options, dict) or not chunking_options:
            return self.audio_chunker

        # Base from current engine chunker.
        silence_threshold_db = -40.0
        try:
            if getattr(self.audio_chunker, "silence_threshold", None):
                silence_threshold_db = 20.0 * math.log10(float(self.audio_chunker.silence_threshold))
        except Exception:
            silence_threshold_db = -40.0

        base_strategy = getattr(self.audio_chunker, "strategy", "silence")
        strategy = chunking_options.get("strategy", base_strategy)
        if not isinstance(strategy, str) or not strategy.strip():
            strategy = base_strategy

        max_chunk = float(chunking_options.get("max_chunk_duration_s", self.audio_chunker.max_chunk_duration))
        min_chunk = float(chunking_options.get("min_chunk_duration_s", self.audio_chunker.min_chunk_duration))
        overlap = float(chunking_options.get("overlap_duration_s", self.audio_chunker.overlap_duration))
        silence_db = float(chunking_options.get("silence_threshold_db", silence_threshold_db))
        min_silence = float(chunking_options.get("min_silence_duration_s", self.audio_chunker.min_silence_duration))

        # Best-effort sanity constraints.
        if max_chunk <= 0:
            max_chunk = self.audio_chunker.max_chunk_duration
        if min_chunk < 0:
            min_chunk = self.audio_chunker.min_chunk_duration
        if overlap < 0:
            overlap = self.audio_chunker.overlap_duration

        return AudioChunker(
            max_chunk_duration=max_chunk,
            min_chunk_duration=min_chunk,
            overlap_duration=overlap,
            silence_threshold_db=silence_db,
            min_silence_duration=min_silence,
            strategy=str(strategy).strip().lower(),
        )

    def _get_request_backend_kwargs(self, asr_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return backend kwargs derived from `asr_options.backend` (reserved keys removed)."""
        backend_options = None
        if isinstance(asr_options, dict):
            backend_options = asr_options.get("backend")

        if not isinstance(backend_options, dict) or not backend_options:
            return {}

        reserved = {
            "input",
            "audio_input",
            "hotword",
            "hotwords",
            "with_speaker",
            "cache",
            "is_final",
        }
        return {k: v for k, v in backend_options.items() if isinstance(k, str) and k not in reserved}

    @staticmethod
    def _normalize_target_backend(value: object) -> Optional[str]:
        """Normalize per-request router target backend override.

        This is a router-only feature used by single-port deployments:
        frontend sends `target_backend=...` to the router, and the router forwards
        the request to the chosen model container inside the docker network.
        """
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        lowered = s.lower()
        if lowered in ("auto", "default"):
            return None
        return lowered

    @staticmethod
    def _is_router_backend(backend: object) -> bool:
        try:
            from src.models.backends.router import RouterBackend

            return isinstance(backend, RouterBackend)
        except Exception:
            return False

    def _backend_transcribe(
        self,
        backend,
        *,
        audio_input,
        hotwords: Optional[str],
        with_speaker: bool,
        target_backend: Optional[str],
        backend_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Call backend.transcribe with router target override (best-effort)."""
        call_kwargs: Dict[str, Any] = dict(backend_kwargs or {})
        if target_backend and self._is_router_backend(backend):
            # RouterBackend consumes and strips this key before calling the actual backend.
            call_kwargs["target_backend"] = target_backend
        return backend.transcribe(
            audio_input,
            hotwords=hotwords,
            with_speaker=with_speaker,
            **call_kwargs,
        )

    def _backend_transcribe_batch(
        self,
        backend,
        *,
        audio_inputs: List[Any],
        hotwords: Optional[str],
        with_speaker: bool,
        target_backend: Optional[str],
        backend_kwargs: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Call backend.transcribe_batch with router target override (best-effort).

        Notes:
        - Unit tests often patch `model_manager.backend` with a MagicMock; in that
          case we intentionally fall back to per-item `transcribe()` calls so the
          mock expectations remain stable.
        - Real backends inherit from ASRBackend and provide a default
          `transcribe_batch` implementation (per-item) even if they don't support
          optimized batching.
        """
        call_kwargs: Dict[str, Any] = dict(backend_kwargs or {})
        if target_backend and self._is_router_backend(backend):
            call_kwargs["target_backend"] = target_backend

        try:
            from src.models.backends.base import ASRBackend as _ASRBackend
        except Exception:
            _ASRBackend = None  # type: ignore[assignment]

        if _ASRBackend is None or not isinstance(backend, _ASRBackend):
            return [
                self._backend_transcribe(
                    backend,
                    audio_input=x,
                    hotwords=hotwords,
                    with_speaker=with_speaker,
                    target_backend=target_backend,
                    backend_kwargs=backend_kwargs,
                )
                for x in audio_inputs
            ]

        return backend.transcribe_batch(
            list(audio_inputs),
            hotwords=hotwords,
            with_speaker=with_speaker,
            **call_kwargs,
        )

    def _get_request_speaker_options(self, asr_options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return request-scoped speaker formatting options.

        `asr_options.speaker.*` overrides Settings defaults for this request only.
        """
        speaker_options = None
        if isinstance(asr_options, dict):
            speaker_options = asr_options.get("speaker")
        if not isinstance(speaker_options, dict):
            speaker_options = {}

        label_style = speaker_options.get("label_style", settings.speaker_label_style)
        if not isinstance(label_style, str) or not label_style.strip():
            label_style = settings.speaker_label_style
        label_style = str(label_style).strip().lower()
        if label_style not in ("zh", "numeric"):
            label_style = settings.speaker_label_style

        return {
            "label_style": label_style,
            "turn_merge_enable": bool(
                speaker_options.get("turn_merge_enable", settings.speaker_turn_merge_enable)
            ),
            "turn_merge_gap_ms": int(
                speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)
            ),
            "turn_merge_min_chars": int(
                speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
            ),
        }

    def _apply_corrections(
        self,
        text: str,
        *,
        post_processor: Optional[TextPostProcessor] = None,
        correction_pipeline: Optional[str] = None,
    ) -> Tuple[str, List[Tuple[str, str, float]]]:
        """应用纠错管线

        按 correction_pipeline 配置的顺序执行各纠错步骤。
        默认顺序: hotword → rules → pycorrector → post_process

        Returns:
            (纠错后文本, 相似词候选列表 [(原词, 热词, 分数), ...])
        """
        original = text
        all_similars: List[Tuple[str, str, float]] = []
        pipeline_str = correction_pipeline or settings.correction_pipeline
        pipeline = [s.strip() for s in pipeline_str.split(',') if s.strip()]
        pp = post_processor or self.post_processor

        for step in pipeline:
            if step == "hotword" and self._hotwords_loaded and text:
                prev = text
                correction = self.corrector.correct(text)
                text = correction.text
                all_similars.extend(correction.similars)
                if text != prev:
                    logger.debug(f"Hotword correction: {prev!r} -> {text!r}")

            elif step == "rules" and self._rules_loaded and text:
                prev = text
                text = self.rule_corrector.substitute(text)
                if text != prev:
                    logger.debug(f"Rule correction: {prev!r} -> {text!r}")

            elif step == "pycorrector" and self._text_correct_enabled and text and self.text_corrector:
                prev = text
                text, errors = self.text_corrector.correct(text)
                if text != prev:
                    logger.debug(f"Text correction: {prev!r} -> {text!r}, errors={errors}")

            elif step == "post_process":
                text = pp.process(text)

        if text != original:
            logger.debug(f"Total correction: {original!r} -> {text!r}")

        return text, all_similars

    def _filter_low_confidence(
        self,
        sentence_info: List[Dict[str, Any]],
        threshold: float = 0.6,
    ) -> List[Dict[str, Any]]:
        """标记并处理低置信度片段

        对低置信度的句子进行额外纠错处理。
        支持 pycorrector 和 LLM 两种回退策略。

        Args:
            sentence_info: 句子信息列表
            threshold: 置信度阈值

        Returns:
            处理后的句子信息列表
        """
        if threshold <= 0:
            return sentence_info

        fallback = settings.confidence_fallback

        for sent in sentence_info:
            confidence = sent.get('confidence', 1.0)
            if confidence < threshold:
                sent['low_confidence'] = True
                text = sent.get('text', '')

                if not text:
                    continue

                if fallback == "pycorrector" and self.text_corrector:
                    corrected, _ = self.text_corrector.correct(text)
                    if corrected != text:
                        logger.debug(f"Low confidence ({confidence:.2f}) pycorrector: {text!r} -> {corrected!r}")
                        sent['text'] = corrected
                        sent['correction_method'] = 'pycorrector'

                elif fallback == "llm" and settings.llm_enable:
                    # 使用 LLM 进行纠错 (异步转同步)
                    try:
                        import asyncio
                        try:
                            loop = asyncio.get_event_loop()
                            corrected = loop.run_until_complete(
                                self._apply_llm_polish(text, role="corrector")
                            )
                        except RuntimeError:
                            corrected = asyncio.run(
                                self._apply_llm_polish(text, role="corrector")
                            )
                        if corrected and corrected != text:
                            logger.debug(f"Low confidence ({confidence:.2f}) LLM: {text!r} -> {corrected!r}")
                            sent['text'] = corrected
                            sent['correction_method'] = 'llm'
                    except Exception as e:
                        logger.warning(f"LLM correction failed for low confidence segment: {e}")

        return sentence_info

    @staticmethod
    def _dedupe_similars(similars: List[Tuple[str, str, float]]) -> List[Tuple[str, str, float]]:
        """去重相似词候选，同一 (原词, 热词) 对只保留最高分"""
        seen: Dict[tuple, float] = {}
        for orig, hw, score in similars:
            key = (orig, hw)
            if key not in seen or score > seen[key]:
                seen[key] = score
        return [(k[0], k[1], v) for k, v in sorted(seen.items(), key=lambda x: -x[1])]

    @staticmethod
    def _estimate_audio_duration_ms(
        audio_input: Union[bytes, str, Path],
        *,
        sample_rate: int = 16000,
    ) -> int:
        """Best-effort estimate audio duration (ms) from common Xiyu inputs.

        - bytes: usually PCM16LE 16k mono (API standardized). If it looks like WAV, parse header.
        - str/Path: if it's a WAV file, parse via stdlib `wave` (no ffmpeg dependency).
        """
        try:
            sr = int(sample_rate) if int(sample_rate) > 0 else 16000
        except Exception:
            sr = 16000

        if isinstance(audio_input, (bytes, bytearray)):
            b = bytes(audio_input)
            if not b:
                return 0
            try:
                from src.core.audio.pcm import is_wav_bytes

                if is_wav_bytes(b):
                    import io
                    import wave

                    with wave.open(io.BytesIO(b), "rb") as wf:
                        frames = wf.getnframes()
                        wav_sr = wf.getframerate() or sr
                        if wav_sr <= 0:
                            wav_sr = sr
                        return max(0, int(float(frames) / float(wav_sr) * 1000.0))
            except Exception:
                # Fall back to PCM estimation below.
                pass

            # Assume PCM16LE mono.
            return max(0, int(float(len(b)) / float(2 * sr) * 1000.0))

        if isinstance(audio_input, (str, Path)):
            p = Path(audio_input)
            if p.suffix.lower() != ".wav":
                return 0
            try:
                import wave

                with wave.open(str(p), "rb") as wf:
                    frames = wf.getnframes()
                    wav_sr = wf.getframerate() or sr
                    if wav_sr <= 0:
                        wav_sr = sr
                    return max(0, int(float(frames) / float(wav_sr) * 1000.0))
            except Exception:
                return 0

        return 0

    @staticmethod
    def _fallback_sentence_info_from_text(
        text: str,
        *,
        duration_ms: int,
    ) -> List[Dict[str, Any]]:
        """Build pseudo sentence_info when backend doesn't provide timestamps.

        This is used for remote/text-only backends (e.g. Qwen3) so the frontend
        still has a usable timeline/SRT export. Timestamps are approximate:
        segments are split by major punctuation and assigned time proportionally
        to character length.
        """
        t = str(text or "").strip()
        if not t:
            return []

        dur = int(duration_ms) if isinstance(duration_ms, int) else 0
        if dur < 0:
            dur = 0

        import re

        # Split by sentence-ending punctuation (keep punctuation in the segment).
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?])\s*", t) if p and p.strip()]
        if not parts:
            parts = [t]

        def _seg_len(s: str) -> int:
            # Length excluding whitespace for more stable proportional allocation.
            x = re.sub(r"\s+", "", s)
            return max(1, len(x))

        lens = [_seg_len(p) for p in parts]
        total = sum(lens) or 1

        out: List[Dict[str, Any]] = []
        cursor = 0
        acc = 0
        for i, p in enumerate(parts):
            seg_len = lens[i]
            if i == len(parts) - 1:
                end = dur
            else:
                end = int(round(float(acc + seg_len) / float(total) * float(dur)))
            if end < cursor:
                end = cursor
            out.append(
                {
                    "text": p,
                    "start": cursor,
                    "end": end,
                }
            )
            cursor = end
            acc += seg_len

        return out

    async def _apply_llm_polish(
        self,
        text: str,
        role: str = "default",
        prev_context: Optional[str] = None,
        next_context: Optional[str] = None,
        similarity_candidates: Optional[List[Tuple[str, str, float]]] = None,
    ) -> str:
        """应用 LLM 润色

        Args:
            text: 待润色文本
            role: LLM 角色
            prev_context: 前文上下文
            next_context: 后文上下文
            similarity_candidates: 相似词候选 [(原词, 热词, 分数), ...]
        """
        if not text:
            return text

        # 获取角色
        role_obj = get_role(role)

        # 构建提示词
        prompt_builder = PromptBuilder(system_prompt=role_obj.system_prompt)

        # 获取纠错历史上下文
        rectify_context = None
        if self._rectify_loaded:
            rectify_context = self.rectification_rag.format_prompt(text, top_k=3)

        # 构建消息
        messages = prompt_builder.build(
            user_content=text,
            hotwords=(
                (self._context_hotwords_list[:50] if self._context_hotwords_list else None)
                or (self._hotwords_list[:50] if self._hotwords_list else None)
            ),
            similarity_candidates=similarity_candidates,
            rectify_context=rectify_context,
            prev_context=prev_context,
            next_context=next_context,
            include_history=False
        )

        # 转换为 LLMMessage
        llm_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]

        # 调用 LLM
        result_parts = []
        async for chunk in self.llm_client.chat(llm_messages, stream=False):
            result_parts.append(chunk)

        polished = "".join(result_parts).strip()
        return polished if polished else text

    async def _apply_llm_fulltext_polish(
        self,
        text: str,
        max_chars: int = 2000,
        similarity_candidates: Optional[List[Tuple[str, str, float]]] = None,
    ) -> str:
        """应用 LLM 全文纠错

        使用专门的 corrector 角色对全文进行一次性纠错，
        利用完整上下文提升一致性。

        Args:
            text: 待纠错全文
            max_chars: 最大字符数限制
            similarity_candidates: 相似词候选 [(原词, 热词, 分数), ...]

        Returns:
            纠错后的文本
        """
        if not text:
            return text

        # 超长文本截断
        if len(text) > max_chars:
            logger.warning(f"Text too long for fulltext polish ({len(text)} > {max_chars}), truncating")
            text = text[:max_chars]

        # 使用 corrector 角色
        role_obj = get_role("corrector")
        prompt_builder = PromptBuilder(system_prompt=role_obj.system_prompt)

        # 获取纠错历史上下文
        rectify_context = None
        if self._rectify_loaded:
            rectify_context = self.rectification_rag.format_prompt(text[:200], top_k=5)

        # 构建消息
        messages = prompt_builder.build(
            user_content=role_obj.format_user_input(text),
            hotwords=(
                (self._context_hotwords_list[:50] if self._context_hotwords_list else None)
                or (self._hotwords_list[:50] if self._hotwords_list else None)
            ),
            similarity_candidates=similarity_candidates,
            rectify_context=rectify_context,
            include_history=False
        )

        # 转换为 LLMMessage
        llm_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]

        # 调用 LLM
        result_parts = []
        async for chunk in self.llm_client.chat(llm_messages, stream=False):
            result_parts.append(chunk)

        polished = "".join(result_parts).strip()
        if polished:
            logger.debug(f"Fulltext LLM correction applied ({len(text)} -> {len(polished)} chars)")
        return polished if polished else text

    async def _apply_llm_polish_with_context(
        self,
        sentences: List[Dict[str, Any]],
        role: str = "default",
        context_sentences: int = 1,
        similarity_candidates: Optional[List[Tuple[str, str, float]]] = None,
    ) -> List[Dict[str, Any]]:
        """对句子列表应用带上下文的 LLM 润色

        Args:
            sentences: 句子列表 [{"text": "...", ...}, ...]
            role: LLM 角色
            context_sentences: 上下文句子数
            similarity_candidates: 相似词候选 [(原词, 热词, 分数), ...]

        Returns:
            润色后的句子列表
        """
        if not sentences or context_sentences <= 0:
            # 不使用上下文，逐句处理
            for sent in sentences:
                if sent.get("text"):
                    sent["text"] = await self._apply_llm_polish(
                        sent["text"], role=role, similarity_candidates=similarity_candidates
                    )
            return sentences

        # 使用上下文处理
        for i, sent in enumerate(sentences):
            if not sent.get("text"):
                continue

            # 构建上下文
            prev_texts = []
            for j in range(max(0, i - context_sentences), i):
                if sentences[j].get("text"):
                    prev_texts.append(sentences[j]["text"])

            next_texts = []
            for j in range(i + 1, min(len(sentences), i + 1 + context_sentences)):
                if sentences[j].get("text"):
                    next_texts.append(sentences[j]["text"])

            prev_context = " ".join(prev_texts) if prev_texts else None
            next_context = " ".join(next_texts) if next_texts else None

            sent["text"] = await self._apply_llm_polish(
                sent["text"],
                role=role,
                prev_context=prev_context,
                next_context=next_context,
                similarity_candidates=similarity_candidates,
            )

        return sentences

    async def _apply_llm_batch_polish(
        self,
        sentences: List[Dict[str, Any]],
        role: str = "default",
        batch_size: int = 5,
    ) -> List[Dict[str, Any]]:
        """批量 LLM 润色 - 将多个句子合并为一个请求

        将多个句子合并发送给 LLM，减少 API 调用次数，提高效率。

        Args:
            sentences: 句子列表 [{"text": "...", ...}, ...]
            role: LLM 角色
            batch_size: 每批处理的句子数

        Returns:
            润色后的句子列表
        """
        import re as re_module

        if not sentences:
            return sentences

        role_obj = get_role(role)

        for i in range(0, len(sentences), batch_size):
            batch = sentences[i:i + batch_size]
            texts = [s.get('text', '') for s in batch if s.get('text')]

            if not texts:
                continue

            # 合并为编号列表
            combined = "\n".join(f"[{j+1}] {t}" for j, t in enumerate(texts))

            prompt_builder = PromptBuilder(system_prompt=role_obj.system_prompt)
            messages = prompt_builder.build(
                user_content=f"请润色以下语音识别结果，按编号返回：\n{combined}",
                hotwords=(
                    (self._context_hotwords_list[:50] if self._context_hotwords_list else None)
                    or (self._hotwords_list[:50] if self._hotwords_list else None)
                ),
                include_history=False
            )

            llm_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]

            result_parts = []
            async for chunk in self.llm_client.chat(llm_messages, stream=False):
                result_parts.append(chunk)

            polished = "".join(result_parts).strip()

            # 解析返回结果
            if polished:
                pattern = re_module.compile(r'\[(\d+)\]\s*(.+?)(?=\[\d+\]|$)', re_module.DOTALL)
                matches = pattern.findall(polished)

                for num_str, text in matches:
                    idx = int(num_str) - 1
                    if 0 <= idx < len(texts):
                        # 找到对应的原始句子并更新
                        for j, s in enumerate(batch):
                            if s.get('text') == texts[idx]:
                                sentences[i + j]['text'] = text.strip()
                                break

        return sentences

    def transcribe(
        self,
        audio_input: Union[bytes, str, Path],
        with_speaker: bool = False,
        apply_hotword: bool = True,
        apply_llm: bool = False,
        llm_role: str = "default",
        hotwords: Optional[str] = None,
        asr_options: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        执行转写

        Args:
            audio_input: 音频输入（文件路径或字节）
            with_speaker: 是否进行说话人识别
            apply_hotword: 是否应用热词纠错
            apply_llm: 是否应用 LLM 润色
            llm_role: LLM 角色（default/translator/code）
            hotwords: 自定义热词（覆盖已加载的热词）
            asr_options: 每请求 ASR 调参 (preprocess/chunking/backend/postprocess)
            **kwargs: 其他参数传递给 ASR 模型

        Returns:
            转写结果字典
        """
        # 获取后端
        backend = model_manager.backend

        # Respect global enable switch (keep consistent with ensemble behavior).
        if apply_llm and not bool(getattr(settings, "llm_enable", False)):
            apply_llm = False

        # 获取注入热词
        injection_hotwords = self._get_injection_hotwords(hotwords)

        # Router-only: allow per-request backend override without leaking unknown kwargs
        # into underlying ASR libraries (e.g. FunASR AutoModel.generate).
        target_backend = self._normalize_target_backend(kwargs.pop("target_backend", None))

        # Per-request overrides (do not mutate globals).
        post_processor = self._get_request_post_processor(asr_options)
        effective_backend_kwargs: Dict[str, Any] = {}
        effective_backend_kwargs.update(self._get_request_backend_kwargs(asr_options))
        effective_backend_kwargs.update(kwargs)

        raw_result = None

        # ------------------------------------------------------------
        # Speaker external diarizer (forced, best-effort)
        # ------------------------------------------------------------
        if with_speaker and bool(getattr(settings, "speaker_external_diarizer_enable", False)) and str(
            getattr(settings, "speaker_external_diarizer_base_url", "")
        ).strip():
            diarizer_t0 = time.time()
            try:
                speaker_options = self._get_request_speaker_options(asr_options)
                coro = self._transcribe_with_external_diarizer(
                    audio_input,
                    backend=backend,
                    injection_hotwords=injection_hotwords,
                    post_processor=post_processor,
                    effective_backend_kwargs=effective_backend_kwargs,
                    target_backend=target_backend,
                    speaker_options=speaker_options,
                    apply_hotword=apply_hotword,
                    apply_llm=apply_llm,
                    llm_role=llm_role,
                )
                try:
                    out = asyncio.get_event_loop().run_until_complete(coro)
                except RuntimeError:
                    out = asyncio.run(coro)

                if out is not None:
                    metrics.record_diarizer_call(success=True, latency_s=time.time() - diarizer_t0)
                    return out
                raise ValueError("external diarizer returned no segments")
            except Exception as e:
                metrics.record_diarizer_call(success=False, latency_s=time.time() - diarizer_t0)
                backend_name = backend.get_info().get("name", "unknown")
                logger.warning(f"External diarizer failed for backend {backend_name} (ignored): {e}")

                # Failure policy:
                # - If backend supports native speaker, fall back to native path.
                # - Otherwise, ignore with_speaker and return normal transcription.
                if not getattr(backend, "supports_speaker", False):
                    with_speaker = False

        # 检查说话人识别支持（按配置决定：报错 / 回退 / 忽略）
        if with_speaker and not backend.supports_speaker:
            behavior = settings.speaker_unsupported_behavior_effective
            backend_name = backend.get_info().get("name", "unknown")

            if behavior == "ignore":
                logger.warning(
                    f"Backend {backend_name} does not support speaker diarization; "
                    "ignoring with_speaker=true for this request"
                )
                with_speaker = False
            elif behavior == "error":
                raise ValueError("backend does not support speaker diarization")
            else:  # fallback
                logger.warning(
                    f"Backend {backend_name} does not support speaker diarization; "
                    "falling back to PyTorch backend"
                )
                # 回退到 loader (PyTorch) 以支持说话人识别
                raw_result = model_manager.loader.transcribe(
                    audio_input,
                    hotwords=injection_hotwords,
                    with_speaker=True,
                    **effective_backend_kwargs,
                )

        # 使用配置的后端
        if raw_result is None:
            try:
                raw_result = self._backend_transcribe(
                    backend,
                    audio_input=audio_input,
                    hotwords=injection_hotwords,
                    with_speaker=with_speaker,
                    target_backend=target_backend,
                    backend_kwargs=effective_backend_kwargs,
                )
            except Exception as e:
                logger.error(f"ASR transcription failed: {e}")
                raise

        # 提取文本和句子信息
        text = raw_result.get("text", "")
        sentence_info = raw_result.get("sentence_info", [])

        # Some remote/text-only backends don't return sentence timestamps.
        # Build best-effort pseudo sentences so the frontend timeline/SRT exports still work.
        if (not sentence_info) and text:
            try:
                duration_ms = self._estimate_audio_duration_ms(audio_input, sample_rate=16000)
                if duration_ms > 0:
                    sentence_info = self._fallback_sentence_info_from_text(text, duration_ms=duration_ms)
            except Exception:
                # Never break transcription on a UI-only enhancement.
                pass

        # 置信度过滤
        if settings.confidence_threshold > 0:
            sentence_info = self._filter_low_confidence(
                sentence_info, threshold=settings.confidence_threshold
            )

        # 热词纠错 - 收集相似词候选
        all_similars: List[Tuple[str, str, float]] = []
        if apply_hotword:
            text, similars = self._apply_corrections(text, post_processor=post_processor)
            all_similars.extend(similars)
            # 同时纠错每个句子的文本
            for sent in sentence_info:
                sent["text"], sent_similars = self._apply_corrections(
                    sent.get("text", ""),
                    post_processor=post_processor,
                )
                all_similars.extend(sent_similars)

        # 去重相似词候选
        all_similars = self._dedupe_similars(all_similars)

        # 说话人标注
        speaker_options = self._get_request_speaker_options(asr_options) if with_speaker else {}
        speaker_labeler = self.speaker_labeler
        if with_speaker:
            label_style = speaker_options.get("label_style", getattr(self.speaker_labeler, "label_style", "zh"))
            if getattr(self.speaker_labeler, "label_style", "zh") != label_style:
                speaker_labeler = SpeakerLabeler(label_style=str(label_style))

        if with_speaker and sentence_info:
            sentence_info = speaker_labeler.label_speakers(sentence_info)

        # LLM 润色（best-effort）：
        # - 非 speaker：润色 `text`（保持 sentence timestamps）
        # - speaker：按 speaker turns 润色，确保 UI 的 turns/transcript 体现 LLM 结果
        if apply_llm and bool(getattr(settings, "llm_enable", False)):
            if with_speaker and sentence_info:
                try:
                    if bool(speaker_options.get("turn_merge_enable", True)):
                        speaker_turns = build_speaker_turns(
                            sentence_info,
                            gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                            min_chars=int(
                                speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                            ),
                        )
                    else:
                        min_chars = int(
                            speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                        )
                        speaker_turns = [
                            {
                                "speaker": s.get("speaker"),
                                "speaker_id": s.get("speaker_id"),
                                "start": s.get("start", 0),
                                "end": s.get("end", 0),
                                "text": s.get("text", ""),
                                "sentence_count": 1,
                            }
                            for s in sentence_info
                            if len(str(s.get("text", "")).strip()) >= min_chars
                        ]

                    # Apply LLM polish per turn with small context window.
                    try:
                        ctx_n = int(getattr(settings, "llm_context_sentences", 1) or 0)
                    except Exception:
                        ctx_n = 1
                    if ctx_n < 0:
                        ctx_n = 0

                    try:
                        coro = self._apply_llm_polish_with_context(
                            speaker_turns,
                            role=llm_role,
                            context_sentences=ctx_n,
                            similarity_candidates=all_similars,
                        )
                        try:
                            speaker_turns = asyncio.get_event_loop().run_until_complete(coro)
                        except RuntimeError:
                            speaker_turns = asyncio.run(coro)
                    except Exception as e:
                        logger.warning("LLM polish for speaker turns failed (ignored): %s", e)

                    out_sentences = [
                        {
                            "text": str(t.get("text") or ""),
                            "start": int(t.get("start") or 0),
                            "end": int(t.get("end") or 0),
                            "speaker": t.get("speaker"),
                            "speaker_id": t.get("speaker_id"),
                        }
                        for t in speaker_turns
                    ]
                    transcript = speaker_labeler.format_transcript(speaker_turns or out_sentences, include_timestamp=True)
                    text_out = "\n".join(str(t.get("text") or "") for t in speaker_turns).strip() or text

                    return {
                        "text": text_out,
                        "text_accu": None,
                        "sentences": out_sentences,
                        "speaker_turns": speaker_turns,
                        "transcript": transcript,
                        "raw_text": raw_result.get("text", ""),
                    }
                except Exception as e:
                    logger.warning("LLM speaker-turn polish flow failed (ignored): %s", e)

            if text:
                try:
                    if settings.llm_fulltext_enable:
                        coro = self._apply_llm_fulltext_polish(
                            text,
                            max_chars=settings.llm_fulltext_max_chars,
                            similarity_candidates=all_similars,
                        )
                    else:
                        coro = self._apply_llm_polish(text, role=llm_role, similarity_candidates=all_similars)

                    try:
                        text = asyncio.get_event_loop().run_until_complete(coro)
                    except RuntimeError:
                        text = asyncio.run(coro)
                except Exception as e:
                    logger.warning("LLM polish failed (ignored): %s", e)

        # 构建返回结果
        result = {
            "text": text,
            "text_accu": None,
            "sentences": [
                {
                    "text": s.get("text", ""),
                    "start": s.get("start", 0),
                    "end": s.get("end", 0),
                    **({"speaker": s.get("speaker"), "speaker_id": s.get("speaker_id")}
                       if with_speaker else {})
                }
                for s in sentence_info
            ],
            "raw_text": raw_result.get("text", ""),
        }

        # 生成格式化转写稿
        if with_speaker:
            speaker_turns: List[Dict[str, Any]] = []
            if result["sentences"]:
                if bool(speaker_options.get("turn_merge_enable", True)):
                    speaker_turns = build_speaker_turns(
                        result["sentences"],
                        gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                        min_chars=int(
                            speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                        ),
                    )
                else:
                    min_chars = int(
                        speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                    )
                    speaker_turns = [
                        {
                            "speaker": s.get("speaker"),
                            "speaker_id": s.get("speaker_id"),
                            "start": s.get("start", 0),
                            "end": s.get("end", 0),
                            "text": s.get("text", ""),
                            "sentence_count": 1,
                        }
                        for s in result["sentences"]
                        if len(str(s.get("text", "")).strip()) >= min_chars
                    ]

            result["speaker_turns"] = speaker_turns
            transcript_source = speaker_turns or result["sentences"]
            result["transcript"] = speaker_labeler.format_transcript(transcript_source, include_timestamp=True)

        return result

    async def transcribe_async(
        self,
        audio_input: Union[bytes, str, Path],
        with_speaker: bool = False,
        apply_hotword: bool = True,
        apply_llm: bool = False,
        llm_role: str = "default",
        hotwords: Optional[str] = None,
        asr_options: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        异步执行转写（适用于 FastAPI 异步端点）

        Args:
            同 transcribe()

        Returns:
            转写结果字典
        """
        # 获取后端
        backend = model_manager.backend

        # Respect global enable switch (keep consistent with ensemble behavior).
        if apply_llm and not bool(getattr(settings, "llm_enable", False)):
            apply_llm = False

        # 获取注入热词
        injection_hotwords = self._get_injection_hotwords(hotwords)

        # Router-only: allow per-request backend override without leaking unknown kwargs
        # into underlying ASR libraries (e.g. FunASR AutoModel.generate).
        target_backend = self._normalize_target_backend(kwargs.pop("target_backend", None))

        # Per-request overrides (do not mutate globals).
        post_processor = self._get_request_post_processor(asr_options)
        effective_backend_kwargs: Dict[str, Any] = {}
        effective_backend_kwargs.update(self._get_request_backend_kwargs(asr_options))
        effective_backend_kwargs.update(kwargs)

        raw_result = None

        # ------------------------------------------------------------
        # Speaker external diarizer (forced, best-effort)
        # ------------------------------------------------------------
        if with_speaker and bool(getattr(settings, "speaker_external_diarizer_enable", False)) and str(
            getattr(settings, "speaker_external_diarizer_base_url", "")
        ).strip():
            diarizer_t0 = time.time()
            try:
                speaker_options = self._get_request_speaker_options(asr_options)
                out = await self._transcribe_with_external_diarizer(
                    audio_input,
                    backend=backend,
                    injection_hotwords=injection_hotwords,
                    post_processor=post_processor,
                    effective_backend_kwargs=effective_backend_kwargs,
                    target_backend=target_backend,
                    speaker_options=speaker_options,
                    apply_hotword=apply_hotword,
                    apply_llm=apply_llm,
                    llm_role=llm_role,
                )
                if out is not None:
                    metrics.record_diarizer_call(success=True, latency_s=time.time() - diarizer_t0)
                    return out
                raise ValueError("external diarizer returned no segments")
            except Exception as e:
                metrics.record_diarizer_call(success=False, latency_s=time.time() - diarizer_t0)
                backend_name = backend.get_info().get("name", "unknown")
                logger.warning(f"External diarizer failed for backend {backend_name} (ignored): {e}")

                # Failure policy (user expectation):
                # - If backend supports native speaker, fall back to native path.
                # - Otherwise, ignore with_speaker and return normal transcription.
                if not getattr(backend, "supports_speaker", False):
                    with_speaker = False

        # ------------------------------------------------------------
        # Speaker fallback diarization (best-effort)
        # ------------------------------------------------------------
        if with_speaker and not backend.supports_speaker:
            if bool(getattr(settings, "speaker_fallback_diarization_enable", False)) and str(
                getattr(settings, "speaker_fallback_diarization_base_url", "")
            ).strip():
                try:
                    speaker_options = self._get_request_speaker_options(asr_options)
                    out = await self._transcribe_with_speaker_fallback_diarization(
                        audio_input,
                        backend=backend,
                        injection_hotwords=injection_hotwords,
                        post_processor=post_processor,
                        effective_backend_kwargs=effective_backend_kwargs,
                        target_backend=target_backend,
                        speaker_options=speaker_options,
                        apply_hotword=apply_hotword,
                        apply_llm=apply_llm,
                        llm_role=llm_role,
                    )
                    if out is not None:
                        return out
                except Exception as e:
                    backend_name = backend.get_info().get("name", "unknown")
                    logger.warning(
                        f"Speaker fallback diarization failed for backend {backend_name} (ignored): {e}"
                    )

        # 检查说话人识别支持（按配置决定：报错 / 回退 / 忽略）
        if with_speaker and not backend.supports_speaker:
            behavior = settings.speaker_unsupported_behavior_effective
            backend_name = backend.get_info().get("name", "unknown")

            if behavior == "ignore":
                logger.warning(
                    f"Backend {backend_name} does not support speaker diarization; "
                    "ignoring with_speaker=true for this request"
                )
                with_speaker = False
            elif behavior == "error":
                raise ValueError("backend does not support speaker diarization")
            else:  # fallback
                logger.warning(
                    f"Backend {backend_name} does not support speaker diarization; "
                    "falling back to PyTorch backend"
                )
                raw_result = model_manager.loader.transcribe(
                    audio_input,
                    hotwords=injection_hotwords,
                    with_speaker=True,
                    **effective_backend_kwargs,
                )

        # 使用配置的后端
        if raw_result is None:
            try:
                raw_result = self._backend_transcribe(
                    backend,
                    audio_input=audio_input,
                    hotwords=injection_hotwords,
                    with_speaker=with_speaker,
                    target_backend=target_backend,
                    backend_kwargs=effective_backend_kwargs,
                )
            except Exception as e:
                logger.error(f"ASR transcription failed: {e}")
                raise

        # 提取文本和句子信息
        text = raw_result.get("text", "")
        sentence_info = raw_result.get("sentence_info", [])

        # Some remote/text-only backends don't return sentence timestamps.
        # Build best-effort pseudo sentences so the frontend timeline/SRT exports still work.
        if (not sentence_info) and text:
            try:
                duration_ms = self._estimate_audio_duration_ms(audio_input, sample_rate=16000)
                if duration_ms > 0:
                    sentence_info = self._fallback_sentence_info_from_text(text, duration_ms=duration_ms)
            except Exception:
                # Never break transcription on a UI-only enhancement.
                pass

        # 置信度过滤
        if settings.confidence_threshold > 0:
            sentence_info = self._filter_low_confidence(
                sentence_info, threshold=settings.confidence_threshold
            )

        # 热词纠错 - 收集相似词候选
        all_similars: List[Tuple[str, str, float]] = []
        if apply_hotword:
            text, similars = self._apply_corrections(text, post_processor=post_processor)
            all_similars.extend(similars)
            for sent in sentence_info:
                sent["text"], sent_similars = self._apply_corrections(
                    sent.get("text", ""),
                    post_processor=post_processor,
                )
                all_similars.extend(sent_similars)

        # 去重相似词候选
        all_similars = self._dedupe_similars(all_similars)

        # 说话人标注
        speaker_options = self._get_request_speaker_options(asr_options) if with_speaker else {}
        speaker_labeler = self.speaker_labeler
        if with_speaker:
            label_style = speaker_options.get("label_style", getattr(self.speaker_labeler, "label_style", "zh"))
            if getattr(self.speaker_labeler, "label_style", "zh") != label_style:
                speaker_labeler = SpeakerLabeler(label_style=str(label_style))

        if with_speaker and sentence_info:
            sentence_info = speaker_labeler.label_speakers(sentence_info)

        # LLM 润色（best-effort）：
        # - 非 speaker：润色 `text`（保持 sentence timestamps）
        # - speaker：按 speaker turns 润色，确保 UI 的 turns/transcript 体现 LLM 结果
        if apply_llm and bool(getattr(settings, "llm_enable", False)):
            if with_speaker and sentence_info:
                try:
                    if bool(speaker_options.get("turn_merge_enable", True)):
                        speaker_turns = build_speaker_turns(
                            sentence_info,
                            gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                            min_chars=int(
                                speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                            ),
                        )
                    else:
                        min_chars = int(
                            speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                        )
                        speaker_turns = [
                            {
                                "speaker": s.get("speaker"),
                                "speaker_id": s.get("speaker_id"),
                                "start": s.get("start", 0),
                                "end": s.get("end", 0),
                                "text": s.get("text", ""),
                                "sentence_count": 1,
                            }
                            for s in sentence_info
                            if len(str(s.get("text", "")).strip()) >= min_chars
                        ]

                    try:
                        ctx_n = int(getattr(settings, "llm_context_sentences", 1) or 0)
                    except Exception:
                        ctx_n = 1
                    if ctx_n < 0:
                        ctx_n = 0

                    try:
                        speaker_turns = await self._apply_llm_polish_with_context(
                            speaker_turns,
                            role=llm_role,
                            context_sentences=ctx_n,
                            similarity_candidates=all_similars,
                        )
                    except Exception as e:
                        logger.warning("LLM polish for speaker turns failed (ignored): %s", e)

                    out_sentences = [
                        {
                            "text": str(t.get("text") or ""),
                            "start": int(t.get("start") or 0),
                            "end": int(t.get("end") or 0),
                            "speaker": t.get("speaker"),
                            "speaker_id": t.get("speaker_id"),
                        }
                        for t in speaker_turns
                    ]
                    transcript = speaker_labeler.format_transcript(speaker_turns or out_sentences, include_timestamp=True)
                    text_out = "\n".join(str(t.get("text") or "") for t in speaker_turns).strip() or text

                    return {
                        "text": text_out,
                        "text_accu": None,
                        "sentences": out_sentences,
                        "speaker_turns": speaker_turns,
                        "transcript": transcript,
                        "raw_text": raw_result.get("text", ""),
                    }
                except Exception as e:
                    logger.warning("LLM speaker-turn polish flow failed (ignored): %s", e)

            if text:
                try:
                    if settings.llm_fulltext_enable:
                        text = await self._apply_llm_fulltext_polish(
                            text,
                            max_chars=settings.llm_fulltext_max_chars,
                            similarity_candidates=all_similars,
                        )
                    else:
                        text = await self._apply_llm_polish(text, role=llm_role, similarity_candidates=all_similars)
                except Exception as e:
                    logger.warning("LLM polish failed (ignored): %s", e)

        # 构建返回结果
        result = {
            "text": text,
            "text_accu": None,
            "sentences": [
                {
                    "text": s.get("text", ""),
                    "start": s.get("start", 0),
                    "end": s.get("end", 0),
                    **({"speaker": s.get("speaker"), "speaker_id": s.get("speaker_id")}
                       if with_speaker else {})
                }
                for s in sentence_info
            ],
            "raw_text": raw_result.get("text", ""),
        }

        # 生成格式化转写稿
        if with_speaker:
            speaker_turns: List[Dict[str, Any]] = []
            if result["sentences"]:
                if bool(speaker_options.get("turn_merge_enable", True)):
                    speaker_turns = build_speaker_turns(
                        result["sentences"],
                        gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                        min_chars=int(
                            speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                        ),
                    )
                else:
                    min_chars = int(
                        speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                    )
                    speaker_turns = [
                        {
                            "speaker": s.get("speaker"),
                            "speaker_id": s.get("speaker_id"),
                            "start": s.get("start", 0),
                            "end": s.get("end", 0),
                            "text": s.get("text", ""),
                            "sentence_count": 1,
                        }
                        for s in result["sentences"]
                        if len(str(s.get("text", "")).strip()) >= min_chars
                    ]

            result["speaker_turns"] = speaker_turns
            transcript_source = speaker_turns or result["sentences"]
            result["transcript"] = speaker_labeler.format_transcript(transcript_source, include_timestamp=True)

        return result

    async def _transcribe_with_speaker_fallback_diarization(
        self,
        audio_input: Union[bytes, str, Path],
        *,
        backend,
        injection_hotwords: Optional[str],
        post_processor: TextPostProcessor,
        effective_backend_kwargs: Dict[str, Any],
        target_backend: Optional[str],
        speaker_options: Dict[str, Any],
        apply_hotword: bool,
        apply_llm: bool,
        llm_role: str,
    ) -> Optional[Dict[str, Any]]:
        """Best-effort speaker diarization fallback for non-diarizing backends.

        Returns:
            A Xiyu-style result dict with speaker fields when successful, otherwise None.
        """
        base_url = str(getattr(settings, "speaker_fallback_diarization_base_url", "") or "").rstrip("/")
        if not base_url:
            return None

        timeout_s = float(getattr(settings, "speaker_fallback_diarization_timeout_s", 30.0) or 30.0)
        max_turn_duration_s = float(getattr(settings, "speaker_fallback_max_turn_duration_s", 25.0) or 0.0)
        max_turns = int(getattr(settings, "speaker_fallback_max_turns", 200) or 0)

        # Normalize audio into PCM16LE for slicing and WAV for HTTP upload.
        pcm16le = ensure_pcm16le_16k_mono_bytes(audio_input)
        if not pcm16le:
            return None
        wav_bytes = pcm16le_to_wav_bytes(pcm16le, sample_rate=16000, channels=1, sampwidth=2)

        # Request diarization from the helper Xiyu service (usually xiyu-pytorch).
        diar_asr_options: Dict[str, Any] = {}
        label_style = speaker_options.get("label_style")
        if isinstance(label_style, str) and label_style.strip().lower() in ("zh", "numeric"):
            diar_asr_options["speaker"] = {"label_style": str(label_style).strip().lower()}

        data = {
            "with_speaker": "true",
            "apply_hotword": "false",
            "apply_llm": "false",
            "llm_role": "default",
        }
        if diar_asr_options:
            data["asr_options"] = json.dumps(diar_asr_options, ensure_ascii=False)

        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        # The helper Xiyu service is usually on localhost/docker networks.
        # Avoid inheriting proxy env vars that can break httpx client init.
        async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
            resp = await client.post(f"{base_url}/api/v1/transcribe", data=data, files=files)
            resp.raise_for_status()
            obj = resp.json()

        if not isinstance(obj, dict):
            return None
        diar_sentences = obj.get("sentences") or []
        if not isinstance(diar_sentences, list) or not diar_sentences:
            return None

        # Build segments to transcribe: either merged turns or 1:1 sentences.
        turn_merge_enable = bool(speaker_options.get("turn_merge_enable", True))
        if turn_merge_enable:
            segments = build_speaker_turns(
                diar_sentences,
                gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                min_chars=0,  # rely on duration + max_turns; diar text is irrelevant for primary ASR
            )
        else:
            segments = []
            for s in diar_sentences:
                segments.append(
                    {
                        "speaker": s.get("speaker"),
                        "speaker_id": s.get("speaker_id"),
                        "start": s.get("start", 0),
                        "end": s.get("end", 0),
                        "text": "",
                        "sentence_count": 1,
                    }
                )

        # Normalize/validate segments and apply max duration splitting.
        max_turn_duration_ms = int(max_turn_duration_s * 1000) if max_turn_duration_s > 0 else 0
        normalized: List[Dict[str, Any]] = []
        for seg in segments:
            try:
                start_ms = int(seg.get("start", 0) or 0)
            except (TypeError, ValueError):
                start_ms = 0
            try:
                end_ms = int(seg.get("end", start_ms) or start_ms)
            except (TypeError, ValueError):
                end_ms = start_ms
            if end_ms < start_ms:
                end_ms = start_ms

            if max_turn_duration_ms > 0 and (end_ms - start_ms) > max_turn_duration_ms:
                cursor = start_ms
                while cursor < end_ms:
                    sub_end = min(cursor + max_turn_duration_ms, end_ms)
                    normalized.append(
                        {
                            "speaker": seg.get("speaker"),
                            "speaker_id": seg.get("speaker_id"),
                            "start": cursor,
                            "end": sub_end,
                            "text": "",
                            "sentence_count": 1,
                        }
                    )
                    cursor = sub_end
            else:
                normalized.append(
                    {
                        "speaker": seg.get("speaker"),
                        "speaker_id": seg.get("speaker_id"),
                        "start": start_ms,
                        "end": end_ms,
                        "text": "",
                        "sentence_count": int(seg.get("sentence_count", 1) or 1),
                    }
                )

        # Enforce max turns (post-split).
        if max_turns > 0 and len(normalized) > max_turns:
            return None

        speaker_labeler = SpeakerLabeler(label_style=str(speaker_options.get("label_style", "zh")))

        out_sentences: List[Dict[str, Any]] = []
        for seg in normalized:
            start_ms = int(seg.get("start", 0) or 0)
            end_ms = int(seg.get("end", start_ms) or start_ms)
            pcm_slice = slice_pcm16le(pcm16le, start_ms=start_ms, end_ms=end_ms)
            if not pcm_slice:
                continue

            raw = self._backend_transcribe(
                backend,
                audio_input=pcm_slice,
                hotwords=injection_hotwords,
                with_speaker=False,
                target_backend=target_backend,
                backend_kwargs=effective_backend_kwargs,
            )
            seg_text = str((raw or {}).get("text", "") or "")

            # Apply hotword/rules/postprocess corrections on per-segment text.
            all_similars: List[Tuple[str, str, float]] = []
            if apply_hotword:
                seg_text, similars = self._apply_corrections(seg_text, post_processor=post_processor)
                all_similars.extend(similars)

            speaker = seg.get("speaker")
            speaker_id = seg.get("speaker_id")
            try:
                speaker_id_int = int(speaker_id)
            except (TypeError, ValueError):
                speaker_id_int = -1

            speaker_str = str(speaker).strip() if isinstance(speaker, str) else ""
            if not speaker_str and speaker_id_int >= 0:
                speaker_str = speaker_labeler._get_speaker_label(speaker_id_int)
            if not speaker_str:
                speaker_str = "未知"

            out_sentences.append(
                {
                    "text": seg_text,
                    "start": start_ms,
                    "end": end_ms,
                    "speaker": speaker_str,
                    "speaker_id": speaker_id_int,
                }
            )

        if not out_sentences:
            return None

        # Full text (no speaker labels) stays consistent with existing API semantics.
        text = "".join([s.get("text", "") for s in out_sentences])
        if apply_llm:
            if settings.llm_fulltext_enable:
                text = await self._apply_llm_fulltext_polish(
                    text,
                    max_chars=settings.llm_fulltext_max_chars,
                    similarity_candidates=[],
                )
            else:
                text = await self._apply_llm_polish(text, role=llm_role, similarity_candidates=[])

        speaker_turns = [
            {
                "speaker": s["speaker"],
                "speaker_id": s["speaker_id"],
                "start": s["start"],
                "end": s["end"],
                "text": s["text"],
                "sentence_count": 1,
            }
            for s in out_sentences
        ]

        transcript_source = speaker_turns or out_sentences
        transcript = speaker_labeler.format_transcript(transcript_source, include_timestamp=True)

        return {
            "text": text,
            "text_accu": None,
            "sentences": out_sentences,
            "speaker_turns": speaker_turns,
            "transcript": transcript,
            "raw_text": text,
        }

    async def _transcribe_with_external_diarizer(
        self,
        audio_input: Union[bytes, str, Path],
        *,
        backend,
        injection_hotwords: Optional[str],
        post_processor: TextPostProcessor,
        effective_backend_kwargs: Dict[str, Any],
        target_backend: Optional[str],
        speaker_options: Dict[str, Any],
        apply_hotword: bool,
        apply_llm: bool,
        llm_role: str,
    ) -> Optional[Dict[str, Any]]:
        """Best-effort forced diarization via an external diarizer service.

        Returns:
            A Xiyu-style result dict with speaker fields when successful, otherwise None.
        """
        base_url = str(getattr(settings, "speaker_external_diarizer_base_url", "") or "").rstrip("/")
        if not base_url:
            return None

        timeout_s = float(getattr(settings, "speaker_external_diarizer_timeout_s", 30.0) or 30.0)
        max_turn_duration_s = float(getattr(settings, "speaker_external_diarizer_max_turn_duration_s", 25.0) or 0.0)
        max_turns = int(getattr(settings, "speaker_external_diarizer_max_turns", 200) or 0)

        pcm16le = ensure_pcm16le_16k_mono_bytes(audio_input)
        if not pcm16le:
            return None

        wav_bytes = pcm16le_to_wav_bytes(pcm16le, sample_rate=16000, channels=1, sampwidth=2)
        duration_ms = (len(pcm16le) // 2) * 1000 // 16000

        raw_segments = await fetch_diarizer_segments(
            base_url=base_url,
            wav_bytes=wav_bytes,
            timeout_s=timeout_s,
        )
        segments = normalize_segments(raw_segments, duration_ms=duration_ms)
        if not segments:
            return None

        label_style = str(speaker_options.get("label_style", "zh"))
        speaker_labeler = SpeakerLabeler(label_style=label_style)

        turn_merge_enable = bool(speaker_options.get("turn_merge_enable", True))
        if turn_merge_enable:
            turns = segments_to_turns(
                segments,
                gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                label_style=label_style,
            )
        else:
            labeled = speaker_labeler.label_speakers(
                [
                    {"spk": s.get("spk"), "start": s.get("start"), "end": s.get("end"), "text": ""}
                    for s in segments
                ],
                spk_key="spk",
            )
            turns = [
                {
                    "speaker": s.get("speaker"),
                    "speaker_id": s.get("speaker_id"),
                    "start": s.get("start", 0),
                    "end": s.get("end", 0),
                    "text": "",
                    "sentence_count": 1,
                }
                for s in labeled
            ]

        # If turns are long, we chunk *inside* each turn with overlap then merge transcripts by text.
        # This avoids mid-word truncation at hard boundaries and reduces overlap duplication.
        #
        # NOTE: We keep diarizer-provided [start,end] for the final sentence so timestamps never overlap.
        max_turn_duration_ms = int(max_turn_duration_s * 1000) if max_turn_duration_s > 0 else 0
        turn_chunker: Optional[AudioChunker] = None
        if max_turn_duration_ms > 0:
            import math

            base_strategy = getattr(self.audio_chunker, "strategy", "silence") or "silence"
            base_overlap = float(getattr(self.audio_chunker, "overlap_duration", 0.5) or 0.5)
            base_min_silence = float(getattr(self.audio_chunker, "min_silence_duration", 0.3) or 0.3)

            silence_threshold_db = -40.0
            try:
                if getattr(self.audio_chunker, "silence_threshold", None):
                    silence_threshold_db = 20.0 * math.log10(float(self.audio_chunker.silence_threshold))
            except Exception:
                silence_threshold_db = -40.0

            max_chunk_s = float(max_turn_duration_s)
            base_min_chunk = float(getattr(self.audio_chunker, "min_chunk_duration", 5.0) or 5.0)
            min_chunk_s = min(max_chunk_s, base_min_chunk)
            if min_chunk_s <= 0:
                # Keep splitting safe even if user config is weird.
                min_chunk_s = max(0.5, max_chunk_s * 0.5)

            overlap_s = base_overlap
            if overlap_s < 0:
                overlap_s = 0.0

            turn_chunker = AudioChunker(
                max_chunk_duration=max_chunk_s,
                min_chunk_duration=min_chunk_s,
                overlap_duration=overlap_s,
                silence_threshold_db=silence_threshold_db,
                min_silence_duration=base_min_silence,
                strategy=str(base_strategy).strip().lower(),
            )

        raw_text_parts: List[str] = []
        out_sentences: List[Dict[str, Any]] = []
        asr_call_count = 0
        for seg in turns:
            try:
                start_ms = int(seg.get("start", 0) or 0)
            except (TypeError, ValueError):
                start_ms = 0
            try:
                end_ms = int(seg.get("end", start_ms) or start_ms)
            except (TypeError, ValueError):
                end_ms = start_ms
            if end_ms < start_ms:
                end_ms = start_ms

            pcm_turn = slice_pcm16le(pcm16le, start_ms=start_ms, end_ms=end_ms)
            if not pcm_turn:
                continue

            raw_turn_text = ""
            if max_turn_duration_ms > 0 and (end_ms - start_ms) > max_turn_duration_ms and turn_chunker is not None:
                from src.core.audio.pcm import pcm16le_bytes_to_float32
                from src.core.text_processor.text_merge import merge_by_text

                # Split by silence (prefer) + overlap within this single-speaker turn.
                audio_f32 = pcm16le_bytes_to_float32(pcm_turn)
                chunks = turn_chunker.split(audio_f32, sample_rate=16000)

                if max_turns > 0 and (asr_call_count + len(chunks)) > max_turns:
                    return None

                for _chunk_audio, start_sample, end_sample in chunks:
                    try:
                        start_i = int(start_sample)
                    except (TypeError, ValueError):
                        start_i = 0
                    try:
                        end_i = int(end_sample)
                    except (TypeError, ValueError):
                        end_i = start_i
                    if end_i <= start_i:
                        continue

                    start_b = max(0, start_i * 2)
                    end_b = min(len(pcm_turn), end_i * 2)
                    if end_b <= start_b:
                        continue

                    pcm_chunk = pcm_turn[start_b:end_b]
                    raw = self._backend_transcribe(
                        backend,
                        audio_input=pcm_chunk,
                        hotwords=injection_hotwords,
                        with_speaker=False,
                        target_backend=target_backend,
                        backend_kwargs=effective_backend_kwargs,
                    )
                    asr_call_count += 1
                    raw_chunk_text = str((raw or {}).get("text", "") or "")
                    raw_turn_text = merge_by_text(raw_turn_text, raw_chunk_text, overlap_chars=20)
            else:
                if max_turns > 0 and (asr_call_count + 1) > max_turns:
                    return None

                raw = self._backend_transcribe(
                    backend,
                    audio_input=pcm_turn,
                    hotwords=injection_hotwords,
                    with_speaker=False,
                    target_backend=target_backend,
                    backend_kwargs=effective_backend_kwargs,
                )
                asr_call_count += 1
                raw_turn_text = str((raw or {}).get("text", "") or "")

            raw_text_parts.append(raw_turn_text)

            # Apply corrections/postprocess after merge to avoid breaking overlap matching.
            seg_text = raw_turn_text
            if apply_hotword:
                seg_text, _similars = self._apply_corrections(seg_text, post_processor=post_processor)

            speaker = seg.get("speaker")
            speaker_id = seg.get("speaker_id")
            try:
                speaker_id_int = int(speaker_id)
            except (TypeError, ValueError):
                speaker_id_int = -1

            speaker_str = str(speaker).strip() if isinstance(speaker, str) else ""
            if not speaker_str and speaker_id_int >= 0:
                speaker_str = speaker_labeler._get_speaker_label(speaker_id_int)
            if not speaker_str:
                speaker_str = "未知"

            out_sentences.append(
                {
                    "text": seg_text,
                    "start": start_ms,
                    "end": end_ms,
                    "speaker": speaker_str,
                    "speaker_id": speaker_id_int,
                }
            )

        if not out_sentences:
            return None

        raw_text = "".join(raw_text_parts)
        text = "".join([s.get("text", "") for s in out_sentences])
        if apply_llm:
            if settings.llm_fulltext_enable:
                text = await self._apply_llm_fulltext_polish(
                    text,
                    max_chars=settings.llm_fulltext_max_chars,
                    similarity_candidates=[],
                )
            else:
                text = await self._apply_llm_polish(text, role=llm_role, similarity_candidates=[])

        # Build speaker turns for readable transcript output.
        speaker_turns: List[Dict[str, Any]] = []
        if out_sentences:
            if bool(speaker_options.get("turn_merge_enable", True)):
                speaker_turns = build_speaker_turns(
                    out_sentences,
                    gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                    min_chars=int(
                        speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                    ),
                )
            else:
                min_chars = int(
                    speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                )
                speaker_turns = [
                    {
                        "speaker": s.get("speaker"),
                        "speaker_id": s.get("speaker_id"),
                        "start": s.get("start", 0),
                        "end": s.get("end", 0),
                        "text": s.get("text", ""),
                        "sentence_count": 1,
                    }
                    for s in out_sentences
                    if len(str(s.get("text", "")).strip()) >= min_chars
                ]

        transcript_source = speaker_turns or out_sentences
        transcript = speaker_labeler.format_transcript(transcript_source, include_timestamp=True)

        return {
            "text": text,
            "text_accu": None,
            "sentences": out_sentences,
            "speaker_turns": speaker_turns,
            "transcript": transcript,
            "raw_text": raw_text,
        }

    async def transcribe_auto_async(
        self,
        audio_input: Union[bytes, str, Path],
        with_speaker: bool = False,
        apply_hotword: bool = True,
        apply_llm: bool = False,
        llm_role: str = "default",
        hotwords: Optional[str] = None,
        asr_options: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Async auto-routing: use chunked transcription for long PCM inputs.

        This is intended for HTTP file transcription where uploads are converted to
        16kHz mono PCM16LE bytes.
        """
        # If diarization is requested but unsupported, we may ignore it so long-audio
        # routing still benefits from chunking.
        backend = model_manager.backend
        if with_speaker and not backend.supports_speaker:
            # If external diarizer or speaker fallback is enabled, keep with_speaker=true so
            # `transcribe_async` can attempt best-effort diarization before deciding to ignore.
            if bool(getattr(settings, "speaker_external_diarizer_enable", False)) and str(
                getattr(settings, "speaker_external_diarizer_base_url", "")
            ).strip():
                pass
            elif bool(getattr(settings, "speaker_fallback_diarization_enable", False)) and str(
                getattr(settings, "speaker_fallback_diarization_base_url", "")
            ).strip():
                pass
            elif settings.speaker_unsupported_behavior_effective == "ignore":
                backend_name = backend.get_info().get("name", "unknown")
                logger.warning(
                    f"Backend {backend_name} does not support speaker diarization; "
                    "ignoring with_speaker=true for this request"
                )
                with_speaker = False

        # Prefer single-pass diarization for meetings; chunking can break speaker consistency.
        if with_speaker:
            return await self.transcribe_async(
                audio_input,
                with_speaker=with_speaker,
                apply_hotword=apply_hotword,
                apply_llm=apply_llm,
                llm_role=llm_role,
                hotwords=hotwords,
                asr_options=asr_options,
                **kwargs,
            )

        # Fast path: if we can cheaply estimate duration from PCM bytes, route long audio.
        if isinstance(audio_input, (bytes, bytearray)):
            b = bytes(audio_input)
            try:
                from src.core.audio.pcm import is_wav_bytes
            except Exception:
                is_wav_bytes = lambda _d: False  # type: ignore[assignment]

            duration_s = 0.0
            if is_wav_bytes(b):
                # WAV bytes: compute duration using stdlib `wave` without decoding full audio.
                try:
                    import io
                    import wave

                    with wave.open(io.BytesIO(b), "rb") as wf:
                        frames = wf.getnframes()
                        sr = wf.getframerate() or 1
                        duration_s = float(frames) / float(sr)
                except Exception:
                    duration_s = 0.0
            else:
                # Raw PCM16LE 16k mono.
                duration_s = float(len(b)) / float(2 * 16000)

            max_chunk_duration_s = float(self._get_request_chunker(asr_options).max_chunk_duration)
            if duration_s > max_chunk_duration_s:
                # Chunked path is heavier; run it off the event loop.
                return await asyncio.to_thread(
                    self.transcribe_long_audio,
                    audio_input,
                    with_speaker=with_speaker,
                    apply_hotword=apply_hotword,
                    apply_llm=apply_llm,
                    llm_role=llm_role,
                    hotwords=hotwords,
                    asr_options=asr_options,
                    **kwargs,
                )

        return await self.transcribe_async(
            audio_input,
            with_speaker=with_speaker,
            apply_hotword=apply_hotword,
            apply_llm=apply_llm,
            llm_role=llm_role,
            hotwords=hotwords,
            asr_options=asr_options,
            **kwargs,
        )

    def transcribe_long_audio(
        self,
        audio_input: Union[bytes, str, Path, np.ndarray],
        with_speaker: bool = False,
        apply_hotword: bool = True,
        apply_llm: bool = False,
        llm_role: str = "default",
        hotwords: Optional[str] = None,
        asr_options: Optional[Dict[str, Any]] = None,
        max_workers: int = 1,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        sample_rate: int = 16000,
        **kwargs
    ) -> Dict[str, Any]:
        """
        长音频智能分块转写

        使用 VAD 检测智能分割长音频，并行处理多个分块，
        最后合并结果并去除重叠。

        Args:
            audio_input: 音频输入（文件路径、字节或 numpy 数组）
            with_speaker: 是否进行说话人识别
            apply_hotword: 是否应用热词纠错
            apply_llm: 是否应用 LLM 润色
            llm_role: LLM 角色
            hotwords: 自定义热词
            max_workers: 并行处理线程数
            sample_rate: 采样率
            **kwargs: 其他参数

        Returns:
            转写结果字典
        """
        from src.core.audio.pcm import (
            is_wav_bytes,
            pcm16le_bytes_to_float32,
            wav_bytes_to_float32,
            float32_to_pcm16le_bytes,
        )

        # Decode into float32 waveform for chunking, and normalize to PCM16LE bytes
        # for backend compatibility (remote backends require bytes, not numpy arrays).
        audio_pcm_bytes: Optional[bytes] = None

        # Respect global enable switch (keep consistent with ensemble behavior).
        if apply_llm and not bool(getattr(settings, "llm_enable", False)):
            apply_llm = False

        # Per-request overrides (do not mutate globals).
        chunker = self._get_request_chunker(asr_options)
        post_processor = self._get_request_post_processor(asr_options)
        preprocess_options = None
        if isinstance(asr_options, dict):
            preprocess_options = asr_options.get("preprocess")
        if not isinstance(preprocess_options, dict):
            preprocess_options = None

        # Request-scoped audio preprocessing:
        # - Short audio (direct): allow full preprocessing (trim/denoise/normalize...)
        # - Long audio (chunked): apply only length-preserving preprocessing per chunk
        #   to keep timestamps stable and avoid holding a full "denoised copy" of hours-long meetings.
        from src.core.audio import AudioPreprocessor

        _pre_cfg = {
            "target_db": settings.audio_normalize_target_db,
            "silence_threshold_db": settings.audio_silence_threshold_db,
            "min_silence_ms": 500,
            "normalize_enable": settings.audio_normalize_enable,
            "normalize_robust_rms_enable": False,
            "normalize_robust_rms_percentile": 95.0,
            "trim_silence_enable": settings.audio_trim_silence_enable,
            "denoise_enable": settings.audio_denoise_enable,
            "denoise_prop": settings.audio_denoise_prop,
            "denoise_backend": settings.audio_denoise_backend,
            "vocal_separate_enable": settings.audio_vocal_separate_enable,
            "vocal_separate_model": settings.audio_vocal_separate_model,
            "device": settings.device,
            "adaptive_enable": settings.audio_adaptive_preprocess,
            "snr_threshold": settings.audio_snr_threshold,
            # Defaults match API layer behavior.
            "remove_dc_offset": True,
            "highpass_enable": bool(getattr(settings, "audio_highpass_enable", False)),
            "highpass_cutoff_hz": float(getattr(settings, "audio_highpass_cutoff_hz", 80.0) or 80.0),
            "lowpass_enable": bool(getattr(settings, "audio_lowpass_enable", False)),
            "lowpass_cutoff_hz": float(getattr(settings, "audio_lowpass_cutoff_hz", 7600.0) or 7600.0),
            "bandpass_enable": bool(getattr(settings, "audio_bandpass_enable", False)),
            "bandpass_low_hz": float(getattr(settings, "audio_bandpass_low_hz", 300.0) or 300.0),
            "bandpass_high_hz": float(getattr(settings, "audio_bandpass_high_hz", 3400.0) or 3400.0),
            "soft_limit_enable": False,
            "soft_limit_target": 0.98,
            "soft_limit_knee": 2.0,
        }
        if preprocess_options:
            for k, v in preprocess_options.items():
                if k in _pre_cfg:
                    _pre_cfg[k] = v

        preprocessor_full = AudioPreprocessor(**_pre_cfg)
        should_preprocess_full = (
            getattr(preprocessor_full, "remove_dc_offset", True)
            or getattr(preprocessor_full, "highpass_enable", False)
            or getattr(preprocessor_full, "lowpass_enable", False)
            or getattr(preprocessor_full, "bandpass_enable", False)
            or getattr(preprocessor_full, "soft_limit_enable", False)
            or preprocessor_full.normalize_enable
            or preprocessor_full.trim_silence_enable
            or preprocessor_full.denoise_enable
            or preprocessor_full.vocal_separate_enable
            or preprocessor_full.adaptive_enable
        )

        # Chunked path must keep length stable; disable operations that can shift timestamps.
        _chunk_cfg = dict(_pre_cfg)
        # NOTE: Adaptive mode may enable trimming internally; disable it for chunking.
        if bool(_chunk_cfg.get("adaptive_enable", False)):
            logger.info("Long-audio chunking: disabling adaptive preprocessing to keep timestamps stable")
        if bool(_chunk_cfg.get("trim_silence_enable", False)):
            logger.info("Long-audio chunking: ignoring trim_silence_enable to keep timestamps stable")
        if bool(_chunk_cfg.get("vocal_separate_enable", False)):
            logger.info("Long-audio chunking: ignoring vocal_separate_enable to keep timestamps stable")

        _chunk_cfg["adaptive_enable"] = False
        _chunk_cfg["trim_silence_enable"] = False
        _chunk_cfg["vocal_separate_enable"] = False

        preprocessor_chunk = AudioPreprocessor(**_chunk_cfg)
        should_preprocess_chunk = (
            getattr(preprocessor_chunk, "remove_dc_offset", True)
            or getattr(preprocessor_chunk, "highpass_enable", False)
            or getattr(preprocessor_chunk, "lowpass_enable", False)
            or getattr(preprocessor_chunk, "bandpass_enable", False)
            or getattr(preprocessor_chunk, "soft_limit_enable", False)
            or preprocessor_chunk.normalize_enable
            or preprocessor_chunk.denoise_enable
        )

        # Enterprise: long-audio chunk checkpointing (resume).
        checkpoint_enable = bool(getattr(settings, "long_audio_checkpoint_enable", False))
        checkpoint_id_opt: Optional[str] = None
        checkpoint_dir_opt: Optional[str] = None
        resume_skip_existing = bool(getattr(settings, "long_audio_checkpoint_resume_skip_existing", True))

        chunking_options = None
        if isinstance(asr_options, dict):
            chunking_options = asr_options.get("chunking")
        if isinstance(chunking_options, dict):
            if isinstance(chunking_options.get("max_workers"), int):
                max_workers = int(chunking_options["max_workers"])
            try:
                infer_batch_size = int(
                    chunking_options.get("infer_batch_size", getattr(settings, "chunk_infer_batch_size", 1)) or 0
                )
            except Exception:
                infer_batch_size = int(getattr(settings, "chunk_infer_batch_size", 1) or 1)
            overlap_chars = int(chunking_options.get("overlap_chars", 20) or 0)
            boundary_reconcile_enable = bool(chunking_options.get("boundary_reconcile_enable", False))
            boundary_reconcile_window_s = float(chunking_options.get("boundary_reconcile_window_s", 1.0) or 0.0)

            # checkpointing (optional)
            checkpoint_enable = bool(chunking_options.get("checkpoint_enable", checkpoint_enable))
            checkpoint_id_opt = str(chunking_options.get("checkpoint_id") or "").strip() or None
            checkpoint_dir_opt = str(chunking_options.get("checkpoint_dir") or "").strip() or None
            resume_skip_existing = bool(chunking_options.get("resume_skip_existing", resume_skip_existing))
        else:
            try:
                infer_batch_size = int(getattr(settings, "chunk_infer_batch_size", 1) or 1)
            except Exception:
                infer_batch_size = 1
            overlap_chars = 20
            boundary_reconcile_enable = False
            boundary_reconcile_window_s = 0.0
        if infer_batch_size <= 0:
            infer_batch_size = 1
        # Avoid accidental huge batches.
        if infer_batch_size > 64:
            infer_batch_size = 64

        if isinstance(audio_input, np.ndarray):
            audio = audio_input.astype(np.float32, copy=False)
            audio_pcm_bytes = float32_to_pcm16le_bytes(audio)

        elif isinstance(audio_input, (bytes, bytearray)):
            data = bytes(audio_input)
            if is_wav_bytes(data):
                audio, sr = wav_bytes_to_float32(data)
                if sr != sample_rate:
                    # Best-effort resample if librosa is available in the runtime.
                    try:
                        import librosa

                        audio = librosa.resample(audio, orig_sr=sr, target_sr=sample_rate)
                    except Exception as e:
                        raise ValueError(
                            f"Unsupported WAV sample_rate={sr}, expected {sample_rate}"
                        ) from e
                audio_pcm_bytes = float32_to_pcm16le_bytes(audio)
            else:
                audio_pcm_bytes = data
                audio = pcm16le_bytes_to_float32(audio_pcm_bytes)

        elif isinstance(audio_input, (str, Path)):
            p = Path(audio_input)
            data = p.read_bytes()
            if not is_wav_bytes(data):
                raise ValueError(
                    f"Unsupported audio file for long-audio chunking: {p.suffix}. "
                    "Please provide 16k PCM bytes (s16le) or a WAV file."
                )
            audio, sr = wav_bytes_to_float32(data)
            if sr != sample_rate:
                try:
                    import librosa

                    audio = librosa.resample(audio, orig_sr=sr, target_sr=sample_rate)
                except Exception as e:
                    raise ValueError(
                        f"Unsupported WAV sample_rate={sr}, expected {sample_rate}"
                    ) from e
            audio_pcm_bytes = float32_to_pcm16le_bytes(audio)

        else:
            raise ValueError(f"Unsupported audio input type: {type(audio_input)}")

        # 检查音频长度，短音频直接转写
        duration = len(audio) / sample_rate
        if duration <= chunker.max_chunk_duration:
            logger.info(f"Audio is short ({duration:.1f}s), using direct transcription")
            if should_preprocess_full:
                audio = preprocessor_full.process(audio, sample_rate=sample_rate, validate=False)
                audio_pcm_bytes = float32_to_pcm16le_bytes(audio)
            return self.transcribe(
                audio_pcm_bytes or audio_input,
                with_speaker=with_speaker,
                apply_hotword=apply_hotword,
                apply_llm=apply_llm,
                llm_role=llm_role,
                hotwords=hotwords,
                asr_options=asr_options,
                **kwargs
            )

        logger.info(f"Long audio detected ({duration:.1f}s), using chunked transcription")

        # Router-only: allow per-request backend override without leaking unknown kwargs
        # into underlying ASR libraries (e.g. FunASR AutoModel.generate).
        target_backend = self._normalize_target_backend(kwargs.pop("target_backend", None))

        effective_backend_kwargs: Dict[str, Any] = {}
        effective_backend_kwargs.update(self._get_request_backend_kwargs(asr_options))
        effective_backend_kwargs.update(kwargs)

        # Prepare backend once (avoid repeating per-chunk work).
        backend = model_manager.backend
        backend_name = backend.get_info().get("name", "unknown")
        backend_type = str((backend.get_info() or {}).get("type") or "").strip().lower()
        # llama.cpp-backed GGUF decoder is not thread-safe; concurrent chunk
        # transcribe can crash the whole process. Force single worker.
        if backend_type == "gguf" and max_workers != 1:
            logger.info(f"GGUF backend detected; forcing chunking max_workers=1 (was {max_workers})")
            max_workers = 1

        if with_speaker and not backend.supports_speaker:
            behavior = settings.speaker_unsupported_behavior_effective
            if behavior == "ignore":
                logger.warning(
                    f"Backend {backend_name} does not support speaker diarization; "
                    "ignoring with_speaker=true for this request"
                )
                with_speaker = False
            elif behavior == "error":
                raise ValueError("backend does not support speaker diarization")
            else:  # fallback
                logger.warning(
                    f"Backend {backend_name} does not support speaker diarization; "
                    "falling back to PyTorch backend for long-audio chunking"
                )

        if with_speaker:
            logger.warning(
                "with_speaker=true with chunking may produce inconsistent speaker mapping/turns; "
                "prefer non-chunked transcription when possible."
            )

        injection_hotwords = self._get_injection_hotwords(hotwords)

        # 分割音频
        chunks = chunker.split(audio, sample_rate)
        total_chunks = len(chunks)

        # ------------------------------------------------------------
        # Chunk checkpointing + resume (enterprise)
        # ------------------------------------------------------------
        checkpoint_job_dir: Optional[Path] = None
        checkpoint_chunks_dir: Optional[Path] = None
        completed_by_idx: Dict[int, Dict[str, Any]] = {}

        if checkpoint_enable:
            from hashlib import sha1
            from datetime import datetime

            root_override = str(
                checkpoint_dir_opt
                or getattr(settings, "long_audio_checkpoint_dir", "")
                or ""
            ).strip()
            root_dir = Path(root_override) if root_override else (settings.outputs_dir / "jobs")
            try:
                root_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            audio_sha1 = ""
            try:
                if audio_pcm_bytes:
                    audio_sha1 = sha1(audio_pcm_bytes).hexdigest()
            except Exception:
                audio_sha1 = ""

            checkpoint_id = checkpoint_id_opt
            if not checkpoint_id:
                try:
                    cfg_obj = {
                        "backend_type": backend_type,
                        "target_backend": target_backend,
                        "chunking": {
                            "strategy": getattr(chunker, "strategy", None),
                            "max_chunk_duration": float(getattr(chunker, "max_chunk_duration", 0.0) or 0.0),
                            "min_chunk_duration": float(getattr(chunker, "min_chunk_duration", 0.0) or 0.0),
                            "overlap_duration": float(getattr(chunker, "overlap_duration", 0.0) or 0.0),
                            "infer_batch_size": int(infer_batch_size),
                            "overlap_chars": int(overlap_chars),
                        },
                        # Keep the id stable but small: don't include every knob.
                        "preprocess": {
                            "denoise_enable": bool(_chunk_cfg.get("denoise_enable")),
                            "denoise_backend": str(_chunk_cfg.get("denoise_backend") or ""),
                            "normalize_enable": bool(_chunk_cfg.get("normalize_enable")),
                            "highpass_enable": bool(_chunk_cfg.get("highpass_enable")),
                            "lowpass_enable": bool(_chunk_cfg.get("lowpass_enable")),
                            "bandpass_enable": bool(_chunk_cfg.get("bandpass_enable")),
                        },
                    }
                    cfg_json = json.dumps(cfg_obj, sort_keys=True, ensure_ascii=False)
                    cfg_sha1 = sha1(cfg_json.encode("utf-8")).hexdigest()
                except Exception:
                    cfg_sha1 = ""

                seed = (audio_sha1 or cfg_sha1 or "job")[:12]
                suffix = (cfg_sha1 or "00000000")[:8]
                checkpoint_id = f"job-{seed}-{suffix}"

            checkpoint_job_dir = root_dir / str(checkpoint_id)
            checkpoint_chunks_dir = checkpoint_job_dir / "chunks"
            try:
                checkpoint_chunks_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                checkpoint_chunks_dir = None

            meta_path = checkpoint_job_dir / "meta.json"
            if not meta_path.exists():
                try:
                    meta = {
                        "checkpoint_id": str(checkpoint_id),
                        "created_at": datetime.utcnow().isoformat() + "Z",
                        "audio_sha1": audio_sha1,
                        "sample_rate": int(sample_rate),
                        "duration_s": float(duration),
                        "backend": {
                            "type": backend_type,
                            "name": backend_name,
                            "target_backend": target_backend,
                        },
                        "chunking": {
                            "strategy": getattr(chunker, "strategy", None),
                            "max_chunk_duration": float(getattr(chunker, "max_chunk_duration", 0.0) or 0.0),
                            "min_chunk_duration": float(getattr(chunker, "min_chunk_duration", 0.0) or 0.0),
                            "overlap_duration": float(getattr(chunker, "overlap_duration", 0.0) or 0.0),
                            "infer_batch_size": int(infer_batch_size),
                            "overlap_chars": int(overlap_chars),
                        },
                        "total_chunks": int(total_chunks),
                        "chunks": [
                            {
                                "idx": int(i),
                                "start_sample": int(start_s),
                                "end_sample": int(end_s),
                            }
                            for i, (_a, start_s, end_s) in enumerate(chunks)
                        ],
                    }
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as e:
                    logger.warning("Failed to write checkpoint meta.json (ignored): %s", e)

            if resume_skip_existing and checkpoint_chunks_dir is not None and total_chunks > 0:
                for p in checkpoint_chunks_dir.glob("*.json"):
                    try:
                        idx = int(p.stem)
                    except Exception:
                        continue
                    if idx < 0 or idx >= total_chunks:
                        continue
                    try:
                        obj = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("success") is True:
                        completed_by_idx[idx] = obj

                if completed_by_idx:
                    logger.info(
                        "Checkpoint resume enabled: loaded %d/%d chunks from %s",
                        len(completed_by_idx),
                        total_chunks,
                        checkpoint_job_dir,
                    )

        def _write_checkpoint_chunk(idx: int, payload: Dict[str, Any]) -> None:
            if checkpoint_chunks_dir is None:
                return
            try:
                tmp = checkpoint_chunks_dir / f"{int(idx):06d}.json.tmp"
                final = checkpoint_chunks_dir / f"{int(idx):06d}.json"
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                tmp.replace(final)
            except Exception as e:
                logger.warning("Write checkpoint chunk failed (idx=%s): %s", idx, e)

        def _write_checkpoint_result(payload: Dict[str, Any]) -> None:
            if checkpoint_job_dir is None:
                return
            try:
                tmp = checkpoint_job_dir / "result.json.tmp"
                final = checkpoint_job_dir / "result.json"
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                tmp.replace(final)
            except Exception as e:
                logger.warning("Write checkpoint result failed (ignored): %s", e)

        # 定义单块转写函数
        def transcribe_chunk(chunk_audio: np.ndarray) -> Dict[str, Any]:
            # Apply length-preserving preprocessing per chunk (optional).
            if should_preprocess_chunk:
                chunk_audio = preprocessor_chunk.process(chunk_audio, sample_rate=sample_rate, validate=False)

            # Always use PCM bytes for backend compatibility (remote backends don't accept numpy).
            chunk_bytes = float32_to_pcm16le_bytes(chunk_audio)

            if with_speaker and not backend.supports_speaker:
                raw_result = model_manager.loader.transcribe(
                    chunk_bytes,
                    hotwords=injection_hotwords,
                    with_speaker=with_speaker,
                    **effective_backend_kwargs,
                )
            else:
                raw_result = self._backend_transcribe(
                    backend,
                    audio_input=chunk_bytes,
                    hotwords=injection_hotwords,
                    with_speaker=with_speaker,
                    target_backend=target_backend,
                    backend_kwargs=effective_backend_kwargs,
                )

            return {
                "text": raw_result.get("text", ""),
                "sentences": raw_result.get("sentence_info", []),
            }
        chunk_results: List[Dict[str, Any]] = []

        def _report_progress(done: int, total: int) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(int(done), int(total))
            except Exception:
                return

        # 并发策略：
        # - max_workers>1：保持旧行为（线程池并行），适合 CPU/不可 batch 的后端
        # - 否则：优先 batch 推理（infer_batch_size>1），否则串行逐块推理
        done = 0
        if max_workers > 1 and infer_batch_size <= 1:
            if checkpoint_enable:
                # Report already-completed chunks first (resume).
                for idx, (_chunk_audio, start_sample, end_sample) in enumerate(chunks):
                    if idx in completed_by_idx:
                        chunk_results.append(completed_by_idx[idx])
                        done += 1
                        _report_progress(done, total_chunks)

                from concurrent.futures import ThreadPoolExecutor, as_completed

                def _run_one(idx: int, chunk_audio: np.ndarray, start_sample: int, end_sample: int) -> tuple[int, Dict[str, Any]]:
                    try:
                        r = transcribe_chunk(chunk_audio)
                        payload = {
                            "idx": int(idx),
                            "start_sample": int(start_sample),
                            "end_sample": int(end_sample),
                            "success": True,
                            "result": r,
                        }
                        return idx, payload
                    except Exception as e:
                        payload = {
                            "idx": int(idx),
                            "start_sample": int(start_sample),
                            "end_sample": int(end_sample),
                            "success": False,
                            "error": str(e),
                        }
                        return idx, payload

                todo = [
                    (idx, chunk_audio, int(start_sample), int(end_sample))
                    for idx, (chunk_audio, start_sample, end_sample) in enumerate(chunks)
                    if idx not in completed_by_idx
                ]

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(_run_one, idx, chunk_audio, start_sample, end_sample): idx
                        for idx, chunk_audio, start_sample, end_sample in todo
                    }
                    for fut in as_completed(futures):
                        idx, payload = fut.result()
                        chunk_results.append(payload)
                        _write_checkpoint_chunk(idx, payload)
                        done += 1
                        _report_progress(done, total_chunks)
            else:
                def _on_chunk_progress(_done: int, _total: int, _res: Dict[str, Any]) -> None:
                    _report_progress(_done, _total)

                chunk_results = chunker.process_parallel(
                    chunks,
                    transcribe_chunk,
                    max_workers=max_workers,
                    on_progress=_on_chunk_progress if progress_callback is not None else None,
                )
                done = total_chunks
        elif infer_batch_size <= 1:
            # 串行逐块推理（最稳定，避免多线程并发占用显存）
            for idx, (chunk_audio, start_sample, end_sample) in enumerate(chunks):
                if checkpoint_enable and idx in completed_by_idx:
                    chunk_results.append(completed_by_idx[idx])
                    done += 1
                    _report_progress(done, total_chunks)
                    continue
                try:
                    r = transcribe_chunk(chunk_audio)
                    payload = {
                        "idx": int(idx),
                        "start_sample": int(start_sample),
                        "end_sample": int(end_sample),
                        "success": True,
                        "result": r,
                    }
                except Exception as e:
                    logger.error(f"Chunk processing failed: {e}")
                    payload = {
                        "idx": int(idx),
                        "start_sample": int(start_sample),
                        "end_sample": int(end_sample),
                        "success": False,
                        "error": str(e),
                    }
                chunk_results.append(payload)
                if checkpoint_enable:
                    _write_checkpoint_chunk(idx, payload)
                done += 1
                _report_progress(done, total_chunks)
        else:
            # Batch 推理：将多个 chunk 打包成 list，一次调用 backend.transcribe_batch()
            batch_specs: List[tuple[int, np.ndarray, int, int]] = []

            def _flush_batch(specs: List[tuple[int, np.ndarray, int, int]]) -> None:
                nonlocal done
                if not specs:
                    return

                batch_bytes: List[bytes] = []
                batch_meta: List[tuple[int, int, int]] = []
                for idx, chunk_audio, start_sample, end_sample in specs:
                    if should_preprocess_chunk:
                        chunk_audio = preprocessor_chunk.process(
                            chunk_audio, sample_rate=sample_rate, validate=False
                        )
                    batch_bytes.append(float32_to_pcm16le_bytes(chunk_audio))
                    batch_meta.append((int(idx), int(start_sample), int(end_sample)))

                try:
                    if with_speaker and not backend.supports_speaker:
                        raw_batch = model_manager.loader.transcribe_batch(
                            batch_bytes,
                            hotwords=injection_hotwords,
                            with_speaker=with_speaker,
                            **effective_backend_kwargs,
                        )
                    else:
                        raw_batch = self._backend_transcribe_batch(
                            backend,
                            audio_inputs=batch_bytes,
                            hotwords=injection_hotwords,
                            with_speaker=with_speaker,
                            target_backend=target_backend,
                            backend_kwargs=effective_backend_kwargs,
                        )

                    if not isinstance(raw_batch, list):
                        raise ValueError("backend.transcribe_batch must return a list")

                    # Normalize length (never crash merge on partial batch outputs).
                    if len(raw_batch) < len(batch_meta):
                        raw_batch = list(raw_batch) + [{}] * (len(batch_meta) - len(raw_batch))

                    for j, (idx, start_sample, end_sample) in enumerate(batch_meta):
                        item = raw_batch[j] if j < len(raw_batch) else {}
                        if not isinstance(item, dict):
                            item = {}
                        payload = {
                            "idx": int(idx),
                            "start_sample": start_sample,
                            "end_sample": end_sample,
                            "success": True,
                            "result": {
                                "text": item.get("text", ""),
                                "sentences": item.get("sentence_info", []),
                            },
                        }
                        chunk_results.append(payload)
                        if checkpoint_enable:
                            _write_checkpoint_chunk(idx, payload)
                        done += 1
                        _report_progress(done, total_chunks)

                except Exception as e:
                    logger.warning(f"Batch chunk transcription failed, falling back to per-chunk: {e}")
                    for (idx, chunk_audio, start_sample, end_sample) in specs:
                        try:
                            r = transcribe_chunk(chunk_audio)
                            payload = {
                                "idx": int(idx),
                                "start_sample": int(start_sample),
                                "end_sample": int(end_sample),
                                "success": True,
                                "result": r,
                            }
                        except Exception as e2:
                            logger.error(f"Chunk processing failed: {e2}")
                            payload = {
                                "idx": int(idx),
                                "start_sample": int(start_sample),
                                "end_sample": int(end_sample),
                                "success": False,
                                "error": str(e2),
                            }
                        chunk_results.append(payload)
                        if checkpoint_enable:
                            _write_checkpoint_chunk(idx, payload)
                        done += 1
                        _report_progress(done, total_chunks)

            for idx, (chunk_audio, start_sample, end_sample) in enumerate(chunks):
                if checkpoint_enable and idx in completed_by_idx:
                    chunk_results.append(completed_by_idx[idx])
                    done += 1
                    _report_progress(done, total_chunks)
                    continue
                batch_specs.append((idx, chunk_audio, int(start_sample), int(end_sample)))
                if len(batch_specs) >= infer_batch_size:
                    _flush_batch(batch_specs)
                    batch_specs = []

            if batch_specs:
                _flush_batch(batch_specs)

        # Optional boundary reconciliation (accuracy-first, slower):
        # Re-transcribe a small window around each chunk split and inject it as a
        # "bridge" result to reduce boundary misses/duplication.
        #
        # NOTE: We currently skip this when diarization is enabled because it may
        # desync `text` vs `sentences`/`transcript` (bridge results don't have stable speaker segments).
        if boundary_reconcile_enable and boundary_reconcile_window_s > 0.0 and not with_speaker:
            try:
                from src.core.audio.boundary_reconcile import build_boundary_bridge_results

                def _transcribe_bridge(pcm16le: bytes) -> str:
                    raw_bridge = self._backend_transcribe(
                        backend,
                        audio_input=pcm16le,
                        hotwords=injection_hotwords,
                        with_speaker=False,
                        target_backend=target_backend,
                        backend_kwargs=effective_backend_kwargs,
                    )
                    return str(raw_bridge.get("text", "") or "")

                bridge_results = build_boundary_bridge_results(
                    audio,
                    chunk_results,
                    sample_rate=sample_rate,
                    overlap_duration_s=chunker.overlap_duration,
                    window_half_s=boundary_reconcile_window_s,
                    transcribe_pcm16le=_transcribe_bridge,
                )
                if bridge_results:
                    logger.info(
                        f"Boundary reconcile enabled: injecting {len(bridge_results)} bridge windows "
                        f"(window_half={boundary_reconcile_window_s:.2f}s)"
                    )
                    chunk_results = chunk_results + bridge_results
            except Exception as e:
                logger.warning(f"Boundary reconcile failed (ignored): {e}")

        # 合并结果
        merged = chunker.merge_results(chunk_results, sample_rate, overlap_chars=overlap_chars)

        raw_text = merged.get("text", "")
        raw_text_accu = merged.get("text_accu", "") or ""
        sentence_info = merged.get("sentences", [])

        # Some remote/text-only backends don't return sentence timestamps.
        # For long-audio chunking this can result in empty timeline/SRT exports.
        if (not sentence_info) and (raw_text_accu or raw_text):
            try:
                duration_ms = max(0, int(float(duration) * 1000.0))
                if duration_ms > 0:
                    base_text = raw_text_accu or raw_text
                    sentence_info = self._fallback_sentence_info_from_text(base_text, duration_ms=duration_ms)
            except Exception:
                pass

        text = raw_text
        text_accu = raw_text_accu

        all_similars: List[Tuple[str, str, float]] = []

        # 合并后统一应用纠错与后处理，避免破坏 chunk overlap 对齐。
        if apply_hotword:
            if text:
                text, similars = self._apply_corrections(text, post_processor=post_processor)
                all_similars.extend(similars)

            if text_accu:
                text_accu, similars_accu = self._apply_corrections(
                    text_accu, post_processor=post_processor
                )
                all_similars.extend(similars_accu)

            # 同时纠错每个句子的文本
            for sent in sentence_info:
                sent["text"], sent_similars = self._apply_corrections(
                    sent.get("text", ""),
                    post_processor=post_processor,
                )
                all_similars.extend(sent_similars)

        # 去重相似词候选
        all_similars = self._dedupe_similars(all_similars)

        # 说话人标注
        speaker_options = self._get_request_speaker_options(asr_options) if with_speaker else {}
        speaker_labeler = self.speaker_labeler
        if with_speaker:
            label_style = speaker_options.get("label_style", getattr(self.speaker_labeler, "label_style", "zh"))
            if getattr(self.speaker_labeler, "label_style", "zh") != label_style:
                speaker_labeler = SpeakerLabeler(label_style=str(label_style))

        if with_speaker and sentence_info:
            sentence_info = speaker_labeler.label_speakers(sentence_info)

        # LLM 润色（best-effort）：
        # - speaker：按 speaker turns 润色，确保 UI 的 turns/transcript 体现 LLM 结果
        # - 非 speaker：优先润色 text_accu（前端默认更偏好它）
        if apply_llm and bool(getattr(settings, "llm_enable", False)):
            if with_speaker and sentence_info:
                try:
                    if bool(speaker_options.get("turn_merge_enable", True)):
                        speaker_turns = build_speaker_turns(
                            sentence_info,
                            gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                            min_chars=int(
                                speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                            ),
                        )
                    else:
                        min_chars = int(
                            speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                        )
                        speaker_turns = [
                            {
                                "speaker": s.get("speaker"),
                                "speaker_id": s.get("speaker_id"),
                                "start": s.get("start", 0),
                                "end": s.get("end", 0),
                                "text": s.get("text", ""),
                                "sentence_count": 1,
                            }
                            for s in sentence_info
                            if len(str(s.get("text", "")).strip()) >= min_chars
                        ]

                    try:
                        ctx_n = int(getattr(settings, "llm_context_sentences", 1) or 0)
                    except Exception:
                        ctx_n = 1
                    if ctx_n < 0:
                        ctx_n = 0

                    try:
                        coro = self._apply_llm_polish_with_context(
                            speaker_turns,
                            role=llm_role,
                            context_sentences=ctx_n,
                            similarity_candidates=all_similars,
                        )
                        try:
                            speaker_turns = asyncio.get_event_loop().run_until_complete(coro)
                        except RuntimeError:
                            speaker_turns = asyncio.run(coro)
                    except Exception as e:
                        logger.warning("LLM polish for speaker turns failed (ignored): %s", e)

                    out_sentences = [
                        {
                            "text": str(t.get("text") or ""),
                            "start": int(t.get("start") or 0),
                            "end": int(t.get("end") or 0),
                            "speaker": t.get("speaker"),
                            "speaker_id": t.get("speaker_id"),
                        }
                        for t in speaker_turns
                    ]
                    transcript = speaker_labeler.format_transcript(speaker_turns or out_sentences, include_timestamp=True)
                    text_out = "\n".join(str(t.get("text") or "") for t in speaker_turns).strip() or text

                    result = {
                        "text": text_out,
                        "text_accu": text_accu if text_accu else None,
                        "sentences": out_sentences,
                        "raw_text": raw_text,
                        "duration": duration,
                        "chunks": len(chunks),
                        "speaker_turns": speaker_turns,
                        "transcript": transcript,
                    }
                    logger.info(f"Long audio transcription completed: {len(chunks)} chunks, {duration:.1f}s")
                    return result
                except Exception as e:
                    logger.warning("LLM speaker-turn polish flow failed (ignored): %s", e)

            target = text_accu if text_accu else text
            if target:
                try:
                    if settings.llm_fulltext_enable:
                        coro = self._apply_llm_fulltext_polish(
                            target,
                            max_chars=settings.llm_fulltext_max_chars,
                            similarity_candidates=all_similars,
                        )
                    else:
                        coro = self._apply_llm_polish(target, role=llm_role, similarity_candidates=all_similars)

                    try:
                        polished = asyncio.get_event_loop().run_until_complete(coro)
                    except RuntimeError:
                        polished = asyncio.run(coro)

                    if polished:
                        text = polished
                        if text_accu:
                            text_accu = polished
                except Exception as e:
                    logger.warning("LLM polish failed (ignored): %s", e)

        result = {
            "text": text,
            "text_accu": text_accu if text_accu else None,
            "sentences": [
                {
                    "text": s.get("text", ""),
                    "start": s.get("start", 0),
                    "end": s.get("end", 0),
                    **({"speaker": s.get("speaker"), "speaker_id": s.get("speaker_id")}
                       if with_speaker else {})
                }
                for s in sentence_info
            ],
            "raw_text": raw_text,
            "duration": duration,
            "chunks": len(chunks),
        }

        # 生成格式化转写稿
        if with_speaker:
            speaker_turns: List[Dict[str, Any]] = []
            if result["sentences"]:
                if bool(speaker_options.get("turn_merge_enable", True)):
                    speaker_turns = build_speaker_turns(
                        result["sentences"],
                        gap_ms=int(speaker_options.get("turn_merge_gap_ms", settings.speaker_turn_merge_gap_ms)),
                        min_chars=int(
                            speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                        ),
                    )
                else:
                    min_chars = int(
                        speaker_options.get("turn_merge_min_chars", settings.speaker_turn_merge_min_chars)
                    )
                    speaker_turns = [
                        {
                            "speaker": s.get("speaker"),
                            "speaker_id": s.get("speaker_id"),
                            "start": s.get("start", 0),
                            "end": s.get("end", 0),
                            "text": s.get("text", ""),
                            "sentence_count": 1,
                        }
                        for s in result["sentences"]
                        if len(str(s.get("text", "")).strip()) >= min_chars
                    ]

            result["speaker_turns"] = speaker_turns
            transcript_source = speaker_turns or result["sentences"]
            result["transcript"] = speaker_labeler.format_transcript(transcript_source, include_timestamp=True)

        logger.info(f"Long audio transcription completed: {len(chunks)} chunks, {duration:.1f}s")
        return result

    def transcribe_streaming(
        self,
        audio_chunk: bytes,
        cache: Dict[str, Any],
        is_final: bool = False,
        hotwords: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """流式转写 (单个音频块)

        注意: 流式转写仅支持 PyTorch 后端。
        其他后端会自动回退到 PyTorch。
        """
        backend = model_manager.backend

        # 获取注入热词
        injection_hotwords = self._get_injection_hotwords(hotwords)

        # 检查流式支持
        if not backend.supports_streaming:
            logger.debug(
                f"Backend {backend.get_info()['name']} does not support streaming, "
                "using PyTorch backend for streaming"
            )
            # 使用 PyTorch 后端的流式功能
            return model_manager.loader._backend.transcribe_streaming(
                audio_chunk,
                cache,
                is_final=is_final,
                hotwords=injection_hotwords,
                **kwargs
            )

        # 使用后端的流式转写
        result = backend.transcribe_streaming(
            audio_chunk,
            cache,
            is_final=is_final,
            hotwords=injection_hotwords,
            **kwargs
        )

        # 应用纠错
        if result.get("text"):
            result["text"], _ = self._apply_corrections(result["text"])

        return result


# 全局引擎实例
transcription_engine = TranscriptionEngine()
