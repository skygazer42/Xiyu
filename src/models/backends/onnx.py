"""ONNX 后端 - 基于 funasr-onnx 的高性能推理"""
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

from .base import ASRBackend
from src.config import settings

logger = logging.getLogger(__name__)

# ONNX 模型 ID
ONNX_MODEL_PARAFORMER = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx"
ONNX_MODEL_PARAFORMER_ONLINE = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx"
ONNX_MODEL_VAD = "iic/speech_fsmn_vad_zh-cn-16k-common-onnx"
ONNX_MODEL_PUNC = "iic/punc_ct-transformer_cn-en-common-vocab471067-large-onnx"


class ONNXBackend(ASRBackend):
    """基于 funasr-onnx 的 ONNX Runtime 后端

    特点：
    - 使用 ONNX Runtime 进行推理，性能提升 2-10x
    - 支持 INT8 量化，进一步降低内存和延迟
    - 支持离线和流式转写
    - 暂不支持说话人识别 (无 ONNX 版本)

    Requirements:
        pip install funasr-onnx onnxruntime
        # 或 GPU 版本
        pip install funasr-onnx onnxruntime-gpu
    """

    def __init__(
        self,
        device: str = "cpu",
        ncpu: int = 4,
        quantize: bool = True,
        intra_threads: int = 4,
        inter_threads: int = 1,
        model_dir: Optional[str] = None,
        model_online_dir: Optional[str] = None,
        vad_model_dir: Optional[str] = None,
        punc_model_dir: Optional[str] = None,
        **kwargs
    ):
        """初始化 ONNX 后端

        Args:
            device: 设备类型 ("cpu" 或 "cuda")
            ncpu: CPU 线程数
            quantize: 是否启用 INT8 量化
            intra_threads: ONNX 推理线程数
            inter_threads: ONNX 并行操作数
            model_dir: 离线 ASR 模型路径（默认自动下载）
            model_online_dir: 流式 ASR 模型路径（默认自动下载）
            vad_model_dir: VAD 模型路径（默认自动下载）
            punc_model_dir: 标点模型路径（默认自动下载）
        """
        self.device = device
        self.ncpu = ncpu
        self.quantize = quantize
        self.intra_threads = intra_threads
        self.inter_threads = inter_threads
        self.model_dir = model_dir or ONNX_MODEL_PARAFORMER
        self.model_online_dir = model_online_dir or ONNX_MODEL_PARAFORMER_ONLINE
        self.vad_model_dir = vad_model_dir or ONNX_MODEL_VAD
        self.punc_model_dir = punc_model_dir or ONNX_MODEL_PUNC

        self._model = None
        self._model_online = None
        self._vad_model = None
        self._vad_model_online = None
        self._punc_model = None
        self._loaded = False

    def load(self) -> None:
        """加载 ONNX 模型"""
        if self._loaded:
            return

        try:
            from funasr_onnx import Paraformer, Fsmn_vad, CT_Transformer
        except ImportError:
            raise ImportError(
                "ONNX 后端需要安装 funasr-onnx: pip install funasr-onnx onnxruntime"
            )

        # 配置 ONNX Runtime 线程
        try:
            import onnxruntime as ort
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = self.intra_threads
            sess_options.inter_op_num_threads = self.inter_threads
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            logger.info(f"ONNX Runtime threads: intra={self.intra_threads}, inter={self.inter_threads}")
        except Exception as e:
            logger.warning(f"Failed to configure ONNX Runtime options: {e}")

        # 加载离线 ASR 模型
        logger.info(f"Loading ONNX ASR model: {self.model_dir}")
        logger.info(f"Quantization: {self.quantize}, Device: {self.device}")
        self._model = Paraformer(
            model_dir=self.model_dir,
            quantize=self.quantize,
        )

        # 加载流式 ASR 模型
        try:
            from funasr_onnx import Paraformer as ParaformerOnline
            logger.info(f"Loading ONNX streaming ASR model: {self.model_online_dir}")
            self._model_online = ParaformerOnline(
                model_dir=self.model_online_dir,
                quantize=self.quantize,
            )
            logger.info("ONNX streaming ASR model loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load streaming ASR model: {e}")
            logger.warning("流式转写将不可用，WebSocket 将回退到 PyTorch 后端")
            self._model_online = None

        # 加载 VAD 模型
        logger.info(f"Loading ONNX VAD model: {self.vad_model_dir}")
        self._vad_model = Fsmn_vad(
            model_dir=self.vad_model_dir,
            quantize=self.quantize,
        )

        # 加载流式 VAD 模型
        try:
            from funasr_onnx import Fsmn_vad_online
            self._vad_model_online = Fsmn_vad_online(
                model_dir=self.vad_model_dir,
                quantize=self.quantize,
            )
            logger.info("ONNX online VAD model loaded successfully")
        except Exception as e:
            logger.warning(f"Failed to load online VAD model: {e}")
            self._vad_model_online = None

        # 加载标点模型
        logger.info(f"Loading ONNX Punctuation model: {self.punc_model_dir}")
        self._punc_model = CT_Transformer(
            model_dir=self.punc_model_dir,
            quantize=self.quantize,
        )

        self._loaded = True
        logger.info("ONNX backend loaded successfully")

    def warmup(self, duration: float = 1.0) -> None:
        """预热模型，消除首次推理延迟

        Args:
            duration: 预热音频时长(秒)
        """
        self._ensure_loaded()

        import numpy as np

        # 生成静默音频进行预热
        sample_rate = 16000
        samples = int(sample_rate * duration)
        silent_audio = np.zeros(samples, dtype=np.float32)

        logger.info(f"Warming up ONNX models with {duration}s silent audio...")

        try:
            # 预热 ASR 模型（funasr-onnx Paraformer 最稳妥的输入是音频文件路径）
            _ = self.transcribe(silent_audio)

            # 预热标点模型
            _ = self._punc_model("测试预热")

            logger.info("ONNX warmup completed")
        except Exception as e:
            logger.warning(f"ONNX warmup failed: {e}")

    def _ensure_loaded(self):
        """确保模型已加载"""
        if not self._loaded:
            self.load()

    @property
    def supports_streaming(self) -> bool:
        """ONNX 后端支持流式（需要加载流式模型）"""
        return self._model_online is not None

    @property
    def supports_speaker(self) -> bool:
        """ONNX 后端暂不支持说话人识别"""
        return False

    def transcribe(
        self,
        audio_input,
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """执行转写

        Args:
            audio_input: 音频输入（文件路径或字节）
            hotwords: 热词（ONNX 后端不直接支持，需通过后处理纠正）
            with_speaker: 忽略（ONNX 不支持说话人）
            **kwargs: 其他参数

        Returns:
            转写结果字典，包含 text 和 sentence_info
        """
        self._ensure_loaded()

        if with_speaker:
            logger.warning("ONNX backend does not support speaker diarization, ignoring with_speaker=True")

        if hotwords:
            logger.debug("ONNX backend: hotwords will be processed via post-processing pipeline")

        # IMPORTANT:
        # funasr-onnx Paraformer loads WAV paths via `librosa.load()`, which pulls in
        # scipy.interpolate/fitpack. In some minimal Docker images this import can
        # fail with a RecursionError. Xiyu already provides 16kHz mono PCM16LE
        # bytes, so we bypass path loading completely by passing a waveform array.
        import numpy as np

        from src.core.audio.pcm import (
            is_wav_bytes,
            wav_bytes_to_float32,
        )

        def _resample_linear(x: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
            """Best-effort linear resample without scipy/librosa dependencies."""
            if orig_sr <= 0 or target_sr <= 0:
                raise ValueError(f"invalid sample rates: orig_sr={orig_sr} target_sr={target_sr}")
            if orig_sr == target_sr:
                return x.astype(np.float32, copy=False)
            if x.size == 0:
                return x.astype(np.float32, copy=False)
            # Use float64 for indices for stability, but output float32.
            ratio = float(target_sr) / float(orig_sr)
            n_out = max(1, int(round(x.size * ratio)))
            src_idx = np.linspace(0.0, float(x.size - 1), num=n_out, dtype=np.float64)
            x64 = x.astype(np.float64, copy=False)
            out = np.interp(src_idx, np.arange(x.size, dtype=np.float64), x64)
            return out.astype(np.float32, copy=False)

        if isinstance(audio_input, (str, Path)):
            # Path inputs are not used by the HTTP API, but keep a small,
            # dependency-light fallback for local usage: only support WAV.
            p = Path(audio_input)
            data = p.read_bytes()
            if not is_wav_bytes(data):
                raise ValueError("ONNX backend only supports WAV path inputs (non-WAV requires ffmpeg preprocessing)")
            audio_f32, sr = wav_bytes_to_float32(data)
            if sr != 16000:
                audio_f32 = _resample_linear(audio_f32, sr, 16000)
            waveform = audio_f32
        elif isinstance(audio_input, (bytes, bytearray)):
            data = bytes(audio_input)
            if is_wav_bytes(data):
                audio_f32, sr = wav_bytes_to_float32(data)
                if sr != 16000:
                    audio_f32 = _resample_linear(audio_f32, sr, 16000)
                waveform = audio_f32
            else:
                # Xiyu internal: raw PCM16LE 16kHz mono
                pcm = data
                if len(pcm) % 2 != 0:
                    pcm = pcm[: len(pcm) - 1]
                waveform = (np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
        elif isinstance(audio_input, np.ndarray):
            waveform = audio_input.astype(np.float32, copy=False)
        else:
            raise ValueError(f"Unsupported audio input type for ONNX backend: {type(audio_input)}")

        try:
            # 直接对完整音频进行 ASR（传入 waveform 可避免 librosa/scipy 依赖链）
            asr_result = self._model(waveform)

            if not asr_result or len(asr_result) == 0:
                return {"text": "", "sentence_info": []}

            # 提取原始文本
            item = asr_result[0]
            if isinstance(item, dict) and "preds" in item:
                raw_text = item["preds"][0] if isinstance(item["preds"], tuple) else str(item["preds"])
            elif isinstance(item, dict) and "text" in item:
                raw_text = item["text"]
            else:
                raw_text = str(item) if item else ""

            if not raw_text:
                return {"text": "", "sentence_info": []}

            # 添加标点
            # funasr_onnx CT_Transformer 返回: ('text_with_punc', [punc_codes])
            try:
                punc_result = self._punc_model(raw_text)
                if isinstance(punc_result, tuple) and len(punc_result) >= 1:
                    text = punc_result[0]
                elif isinstance(punc_result, list) and len(punc_result) > 0:
                    text = punc_result[0] if isinstance(punc_result[0], str) else raw_text
                else:
                    text = raw_text
            except Exception as e:
                logger.warning(f"Punctuation model failed, using raw text: {e}")
                text = raw_text

            return {
                "text": text,
                "sentence_info": [{"text": text, "start": 0, "end": 0}] if text else [],
            }

        except Exception as e:
            logger.error(f"ONNX transcription failed: {e}")
            raise
        finally:
            # No temp files when using waveform inputs.
            pass

    def transcribe_streaming(
        self,
        audio_chunk: bytes,
        cache: Dict[str, Any],
        is_final: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """流式转写（单个音频块）

        使用 Paraformer-online 和 Fsmn_vad_online 进行流式 ASR。

        Args:
            audio_chunk: 音频数据块 (16kHz, 16bit, mono PCM)
            cache: 状态缓存字典，用于维持流式状态
            is_final: 是否为最后一个块
            **kwargs: 其他参数

        Returns:
            转写结果字典，包含 text 和 is_final
        """
        self._ensure_loaded()

        if self._model_online is None:
            raise RuntimeError("流式 ASR 模型未加载，无法进行流式转写")

        import numpy as np

        try:
            # 将 bytes 转为 numpy array (16kHz, 16bit, mono)
            if isinstance(audio_chunk, bytes):
                audio_data = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0
            elif isinstance(audio_chunk, np.ndarray):
                audio_data = audio_chunk.astype(np.float32) if audio_chunk.dtype != np.float32 else audio_chunk
            else:
                raise ValueError(f"不支持的音频输入类型: {type(audio_chunk)}")

            if len(audio_data) == 0:
                return {"text": "", "is_final": is_final}

            # 初始化流式缓存
            if "online_cache" not in cache:
                cache["online_cache"] = {}
            if "vad_cache" not in cache:
                cache["vad_cache"] = {}
            if "accumulated_text" not in cache:
                cache["accumulated_text"] = ""

            # 流式 VAD 检测
            text_parts = []
            if self._vad_model_online is not None:
                vad_result = self._vad_model_online(
                    audio_data,
                    cache=cache["vad_cache"],
                    is_final=is_final,
                )
                # VAD 返回语音段的起止位置
                if vad_result and len(vad_result) > 0:
                    segments = vad_result[0] if isinstance(vad_result, list) else vad_result
                    if isinstance(segments, dict) and "value" in segments:
                        segments = segments["value"]
                    if segments:
                        # 对每个语音段进行在线 ASR
                        for seg in segments if isinstance(segments, list) else [segments]:
                            asr_result = self._model_online(
                                audio_data,
                                cache=cache["online_cache"],
                                is_final=is_final,
                            )
                            if asr_result:
                                text_parts.append(self._extract_text(asr_result))
                else:
                    # VAD 未检测到语音段，仍然送入 ASR（可能在积累中）
                    asr_result = self._model_online(
                        audio_data,
                        cache=cache["online_cache"],
                        is_final=is_final,
                    )
                    if asr_result:
                        text_parts.append(self._extract_text(asr_result))
            else:
                # 无流式 VAD，直接送入 ASR
                asr_result = self._model_online(
                    audio_data,
                    cache=cache["online_cache"],
                    is_final=is_final,
                )
                if asr_result:
                    text_parts.append(self._extract_text(asr_result))

            chunk_text = "".join(text_parts)

            # 如果是最后一个块，添加标点
            if is_final and chunk_text:
                try:
                    punc_result = self._punc_model(chunk_text)
                    if isinstance(punc_result, tuple) and len(punc_result) >= 1:
                        chunk_text = punc_result[0]
                    elif isinstance(punc_result, list) and len(punc_result) > 0:
                        chunk_text = punc_result[0] if isinstance(punc_result[0], str) else chunk_text
                except Exception as e:
                    logger.warning(f"Streaming punctuation failed: {e}")

            return {
                "text": chunk_text,
                "is_final": is_final,
            }

        except Exception as e:
            logger.error(f"ONNX streaming transcription failed: {e}")
            raise

    def _extract_text(self, asr_result) -> str:
        """从 ASR 结果中提取文本"""
        if not asr_result or len(asr_result) == 0:
            return ""

        item = asr_result[0] if isinstance(asr_result, list) else asr_result
        if isinstance(item, dict) and "preds" in item:
            return item["preds"][0] if isinstance(item["preds"], tuple) else str(item["preds"])
        elif isinstance(item, dict) and "text" in item:
            return item["text"]
        elif isinstance(item, str):
            return item
        return str(item) if item else ""

    def unload(self) -> None:
        """释放模型资源"""
        self._model = None
        self._model_online = None
        self._vad_model = None
        self._vad_model_online = None
        self._punc_model = None
        self._loaded = False
        logger.info("ONNX backend models unloaded")

    def get_info(self) -> Dict[str, Any]:
        """获取后端信息"""
        return {
            "name": "ONNXBackend",
            "type": "onnx",
            "device": self.device,
            "quantize": self.quantize,
            "model_dir": self.model_dir,
            "model_online_dir": self.model_online_dir,
            "vad_model_dir": self.vad_model_dir,
            "punc_model_dir": self.punc_model_dir,
            "supports_streaming": self.supports_streaming,
            "supports_hotwords": True,  # 通过后处理支持
            "supports_speaker": False,
        }
