"""PyTorch 后端 - 基于 FunASR AutoModel"""
import logging
from typing import Optional, Dict, Any, List

from funasr import AutoModel

from .base import ASRBackend
from src.config import settings

logger = logging.getLogger(__name__)


class PyTorchBackend(ASRBackend):
    """基于 FunASR AutoModel 的 PyTorch 后端

    这是默认后端，支持完整的 FunASR 功能：
    - 离线转写（VAD + ASR + 标点）
    - 流式转写
    - 说话人识别
    - 热词支持
    """

    def __init__(
        self,
        device: str = "cuda",
        ngpu: int = 1,
        ncpu: int = 4,
        asr_model: str = "paraformer-zh",
        asr_model_online: str = "paraformer-zh-streaming",
        vad_model: str = "fsmn-vad",
        punc_model: str = "ct-punc-c",
        spk_model: Optional[str] = "cam++",
    ):
        self.device = device
        self.ngpu = ngpu
        self.ncpu = ncpu

        self._asr_model_name = asr_model
        self._asr_model_online_name = asr_model_online
        self._vad_model_name = vad_model
        self._punc_model_name = punc_model
        self._spk_model_name = spk_model

        self._asr_model: Optional[AutoModel] = None
        self._asr_model_online: Optional[AutoModel] = None
        self._asr_model_with_spk: Optional[AutoModel] = None
        self._loaded = False

    def load(self) -> None:
        """预加载所有模型"""
        _ = self.asr_model
        _ = self.asr_model_online
        self._loaded = True

    @property
    def asr_model(self) -> AutoModel:
        """获取离线 ASR 模型 (VAD + ASR + Punc)"""
        if self._asr_model is None:
            logger.info(f"Loading offline ASR model: {self._asr_model_name}")
            self._asr_model = AutoModel(
                model=self._asr_model_name,
                vad_model=self._vad_model_name,
                punc_model=self._punc_model_name,
                device=self.device,
                ngpu=self.ngpu,
                ncpu=self.ncpu,
                disable_pbar=True,
                disable_log=True,
            )
            logger.info("Offline ASR model loaded successfully")
        return self._asr_model

    @property
    def asr_model_online(self) -> AutoModel:
        """获取在线流式 ASR 模型"""
        if self._asr_model_online is None:
            logger.info(f"Loading online ASR model: {self._asr_model_online_name}")
            self._asr_model_online = AutoModel(
                model=self._asr_model_online_name,
                device=self.device,
                ngpu=self.ngpu,
                ncpu=self.ncpu,
                disable_pbar=True,
                disable_log=True,
            )
            logger.info("Online ASR model loaded successfully")
        return self._asr_model_online

    @property
    def asr_model_with_spk(self) -> AutoModel:
        """获取带说话人识别的 ASR 模型"""
        if self._asr_model_with_spk is None:
            logger.info("Loading ASR model with speaker diarization")
            self._asr_model_with_spk = AutoModel(
                model=self._asr_model_name,
                vad_model=self._vad_model_name,
                punc_model=self._punc_model_name,
                spk_model=self._spk_model_name,
                device=self.device,
                ngpu=self.ngpu,
                ncpu=self.ncpu,
                disable_pbar=True,
                disable_log=True,
            )
            logger.info("ASR model with speaker loaded successfully")
        return self._asr_model_with_spk

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_speaker(self) -> bool:
        return True

    @property
    def supports_batch(self) -> bool:
        # FunASR AutoModel.generate supports list inputs and performs padded batching internally.
        return True

    def transcribe(
        self,
        audio_input,
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """执行转写

        Args:
            audio_input: 音频输入
            hotwords: 热词字符串
            with_speaker: 是否启用说话人识别
            **kwargs: 其他参数

        Returns:
            转写结果字典，包含 text 和 sentence_info
        """
        params = {
            "input": audio_input,
            "sentence_timestamp": True,
            "batch_size_s": 300,
            # VAD 参数
            "max_single_segment_time": settings.vad_max_segment_ms,
        }
        if hotwords:
            params["hotword"] = hotwords
        params.update(kwargs)

        model = self.asr_model_with_spk if with_speaker else self.asr_model
        result = model.generate(**params)

        if not result:
            return {"text": "", "sentence_info": []}

        raw = result[0]

        # 标准化输出格式
        return {
            "text": raw.get("text", ""),
            "sentence_info": raw.get("sentence_info", []),
            # 保留原始数据供高级用途
            "_raw": raw,
        }

    def transcribe_batch(
        self,
        audio_inputs: List[Any],
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """批量转写（优先走 FunASR 内部 padded batching）。"""
        items = list(audio_inputs or [])
        if not items:
            return []

        params = {
            "input": items,
            "sentence_timestamp": True,
            "batch_size_s": 300,
            "max_single_segment_time": settings.vad_max_segment_ms,
        }
        if hotwords:
            params["hotword"] = hotwords
        params.update(kwargs)

        model = self.asr_model_with_spk if with_speaker else self.asr_model

        def _unwrap_raw(x: Any) -> Optional[Dict[str, Any]]:
            if x is None:
                return None
            if isinstance(x, dict):
                return x
            if isinstance(x, list) and x:
                # Some FunASR variants may wrap per-item outputs in a list.
                first = x[0]
                if isinstance(first, dict):
                    return first
            return None

        try:
            result = model.generate(**params)
        except Exception as e:
            logger.warning(f"FunASR batch generate failed, falling back to per-item: {e}")
            return [
                self.transcribe(x, hotwords=hotwords, with_speaker=with_speaker, **kwargs)
                for x in items
            ]

        if not isinstance(result, list):
            result = [result] if result is not None else []

        out: List[Dict[str, Any]] = []
        for i in range(len(items)):
            raw = _unwrap_raw(result[i] if i < len(result) else None) or {}
            out.append(
                {
                    "text": raw.get("text", "") if isinstance(raw, dict) else "",
                    "sentence_info": raw.get("sentence_info", []) if isinstance(raw, dict) else [],
                    "_raw": raw,
                }
            )

        return out

    def get_info(self) -> Dict[str, Any]:
        base = super().get_info()
        base.update(
            {
                "type": "pytorch",
                "device": self.device,
                "ngpu": self.ngpu,
                "ncpu": self.ncpu,
                "asr_model": self._asr_model_name,
                "vad_model": self._vad_model_name,
                "punc_model": self._punc_model_name,
                "spk_model": self._spk_model_name,
            }
        )
        return base

    def transcribe_streaming(
        self,
        audio_chunk: bytes,
        cache: Dict[str, Any],
        is_final: bool = False,
        hotwords: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """流式转写

        Args:
            audio_chunk: 音频数据块
            cache: 状态缓存
            is_final: 是否为最后一个块
            hotwords: 热词
            **kwargs: 其他参数

        Returns:
            转写结果
        """
        params = {
            "input": audio_chunk,
            "cache": cache.get("asr_cache", {}),
            "is_final": is_final,
        }
        if hotwords:
            params["hotword"] = hotwords
        params.update(kwargs)

        result = self.asr_model_online.generate(**params)

        if result:
            cache["asr_cache"] = result[0].get("cache", {})
            return {
                "text": result[0].get("text", ""),
                "is_final": is_final,
            }

        return {"text": "", "is_final": is_final}

    def unload(self) -> None:
        """释放模型资源"""
        self._asr_model = None
        self._asr_model_online = None
        self._asr_model_with_spk = None
        self._loaded = False

        # 清理 GPU 内存
        self._cleanup_gpu_memory()
        logger.info("PyTorch backend models unloaded")

    def _cleanup_gpu_memory(self) -> None:
        """清理 GPU 内存"""
        if self.device == "cuda":
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
                    logger.debug("GPU memory cache cleared")
            except Exception as e:
                logger.warning(f"Failed to clear GPU cache: {e}")

    def warmup(self, duration: float = 1.0) -> None:
        """预热模型，消除首次推理延迟

        Args:
            duration: 预热音频时长(秒)
        """
        import numpy as np

        # 生成静默音频进行预热
        sample_rate = 16000
        samples = int(sample_rate * duration)
        silent_audio = np.zeros(samples, dtype=np.float32)

        logger.info(f"Warming up PyTorch models with {duration}s silent audio...")

        try:
            # 预热离线模型
            _ = self.asr_model.generate(input=silent_audio)

            # 预热在线模型
            _ = self.asr_model_online.generate(input=silent_audio, cache={}, is_final=True)

            # 清理 GPU 内存
            self._cleanup_gpu_memory()

            logger.info("PyTorch warmup completed")
        except Exception as e:
            logger.warning(f"PyTorch warmup failed: {e}")

    def get_info(self) -> Dict[str, Any]:
        """获取后端信息"""
        return {
            "name": "PyTorchBackend",
            "type": "pytorch",
            "device": self.device,
            "asr_model": self._asr_model_name,
            "asr_model_online": self._asr_model_online_name,
            "vad_model": self._vad_model_name,
            "punc_model": self._punc_model_name,
            "spk_model": self._spk_model_name,
            "supports_streaming": True,
            "supports_hotwords": True,
            "supports_speaker": True,
        }
