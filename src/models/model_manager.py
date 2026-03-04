"""全局模型管理器"""
import logging
from typing import Optional

from src.config import settings
from src.models.backends import ASRBackend, get_backend
from src.models.asr_loader import ASRModelLoader

logger = logging.getLogger(__name__)


class ModelManager:
    """模型管理器单例

    支持多种后端：
    - pytorch: 默认 FunASR PyTorch 后端
    - onnx: ONNX Runtime 高性能后端
    - sensevoice: SenseVoice 高速后端
    """
    _instance: Optional['ModelManager'] = None
    _loader: Optional[ASRModelLoader] = None
    _backend: Optional[ASRBackend] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def backend(self) -> ASRBackend:
        """获取当前 ASR 后端"""
        if self._backend is None:
            backend_type = settings.asr_backend
            logger.info(f"Initializing ASR backend: {backend_type}")

            if backend_type == "pytorch":
                self._backend = get_backend(
                    backend_type="pytorch",
                    device=settings.device,
                    ngpu=settings.ngpu,
                    ncpu=settings.ncpu,
                    asr_model=settings.asr_model,
                    asr_model_online=settings.asr_model_online,
                    vad_model=settings.vad_model,
                    punc_model=settings.punc_model,
                    spk_model=settings.spk_model,
                )
            elif backend_type == "onnx":
                self._backend = get_backend(
                    backend_type="onnx",
                    device=settings.device,
                    ncpu=settings.ncpu,
                    quantize=settings.onnx_quantize,
                )
            elif backend_type == "sensevoice":
                self._backend = get_backend(
                    backend_type="sensevoice",
                    device=settings.device,
                    ngpu=settings.ngpu,
                    ncpu=settings.ncpu,
                    model=settings.sensevoice_model,
                    language=settings.sensevoice_language,
                )
            elif backend_type == "gguf":
                self._backend = get_backend(
                    backend_type="gguf",
                    encoder_path=settings.gguf_encoder_path,
                    ctc_path=settings.gguf_ctc_path,
                    decoder_path=settings.gguf_decoder_path,
                    tokens_path=settings.gguf_tokens_path,
                    lib_dir=settings.gguf_lib_dir,
                )
            elif backend_type == "qwen3":
                self._backend = self._build_remote_backend("qwen3")
            elif backend_type == "vibevoice":
                self._backend = self._build_remote_backend("vibevoice")
            elif backend_type == "router":
                from src.models.backends.router import RouterBackend

                short_backend = self._build_backend_for_router(settings.router_short_backend)
                long_backend = self._build_backend_for_router(settings.router_long_backend)
                self._backend = RouterBackend(
                    short_backend=short_backend,
                    long_backend=long_backend,
                    long_audio_threshold_s=settings.router_long_audio_threshold_s,
                    force_vibevoice_when_with_speaker=settings.router_force_vibevoice_when_with_speaker,
                )
            elif backend_type == "whisper":
                self._backend = get_backend(
                    backend_type="whisper",
                    model=settings.whisper_model,
                    device=settings.device,
                    language=settings.whisper_language,
                    download_root=settings.whisper_download_root,
                )
            else:
                raise ValueError(f"Unknown backend type: {backend_type}")

            logger.info(f"ASR backend initialized: {self._backend.get_info()}")

        return self._backend

    @staticmethod
    def _build_backend_for_router(backend_type: str) -> ASRBackend:
        """Build a backend instance for RouterBackend.

        Router may want to route to:
        - remote ASR servers (qwen3/vibevoice)
        - a local whisper model OR a proxied Xiyu whisper service
        """
        bt = str(backend_type or "").strip().lower()

        if bt in ("qwen3", "vibevoice"):
            return ModelManager._build_remote_backend(bt)

        if bt == "whisper":
            base_url = str(getattr(settings, "whisper_service_base_url", "") or "").strip().rstrip("/")
            if base_url:
                from src.models.backends.xiyu_proxy import XiyuTranscribeProxyBackend

                return XiyuTranscribeProxyBackend(
                    base_url=base_url,
                    timeout_s=float(getattr(settings, "whisper_service_timeout_s", 600.0) or 600.0),
                    name="xiyu-whisper-proxy",
                )

            # Fallback: load Whisper weights locally in this process.
            return get_backend(
                backend_type="whisper",
                model=settings.whisper_model,
                device=settings.device,
                language=settings.whisper_language,
                download_root=settings.whisper_download_root,
            )

        raise ValueError(f"Unknown router backend type: {backend_type}")

    @staticmethod
    def _build_remote_backend(backend_type: str) -> ASRBackend:
        """Build a remote backend from Settings."""
        if backend_type == "qwen3":
            return get_backend(
                backend_type="qwen3",
                base_url=settings.qwen3_asr_base_url,
                model=settings.qwen3_asr_model,
                api_key=settings.qwen3_asr_api_key,
                timeout_s=settings.qwen3_asr_timeout_s,
            )
        if backend_type == "vibevoice":
            return get_backend(
                backend_type="vibevoice",
                base_url=settings.vibevoice_asr_base_url,
                model=settings.vibevoice_asr_model,
                api_key=settings.vibevoice_asr_api_key,
                timeout_s=settings.vibevoice_asr_timeout_s,
                use_chat_completions_fallback=settings.vibevoice_asr_use_chat_completions_fallback,
            )
        raise ValueError(f"Unknown remote backend type: {backend_type}")

    @property
    def loader(self) -> ASRModelLoader:
        """获取 ASR 加载器 (向后兼容)

        注意: 此属性仅用于向后兼容。
        新代码应使用 backend 属性。
        """
        if self._loader is None:
            self._loader = ASRModelLoader(
                device=settings.device,
                ngpu=settings.ngpu,
                ncpu=settings.ncpu,
                asr_model=settings.asr_model,
                asr_model_online=settings.asr_model_online,
                vad_model=settings.vad_model,
                punc_model=settings.punc_model,
                spk_model=settings.spk_model,
            )
        return self._loader

    def preload_models(self, with_speaker: bool = True):
        """预加载模型"""
        backend_type = settings.asr_backend

        if backend_type == "pytorch":
            # PyTorch 后端使用原有的预加载方式
            _ = self.loader.asr_model
            _ = self.loader.asr_model_online
            if with_speaker:
                _ = self.loader.asr_model_with_spk
        else:
            # 其他后端直接加载
            self.backend.load()

    def get_backend_info(self) -> dict:
        """获取当前后端信息"""
        return self.backend.get_info()

    def switch_backend(self, backend_type: str) -> None:
        """切换后端（运行时）

        注意: 切换后端会卸载当前模型并重新加载。
        """
        if self._backend is not None:
            self._backend.unload()
            self._backend = None

        # 临时修改设置
        settings.asr_backend = backend_type
        logger.info(f"Switching to backend: {backend_type}")

        # 重新初始化
        _ = self.backend


model_manager = ModelManager()
