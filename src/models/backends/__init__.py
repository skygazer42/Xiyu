"""ASR 后端模块

提供多种 ASR 后端实现：
- PyTorchBackend: 基于 FunASR AutoModel 的 PyTorch 后端（默认）
- ONNXBackend: 基于 funasr-onnx 的 ONNX Runtime 后端
- SenseVoiceBackend: 基于 SenseVoice 的高速后端
- GGUFBackend: 基于 llama.cpp 的 GGUF 量化后端
"""
from typing import Literal, Optional
import logging

from .base import ASRBackend

logger = logging.getLogger(__name__)

BackendType = Literal["pytorch", "onnx", "sensevoice", "gguf", "qwen3", "vibevoice", "router", "whisper"]


def get_backend(
    backend_type: BackendType = "pytorch",
    device: str = "cuda",
    ngpu: int = 1,
    ncpu: int = 4,
    **kwargs
) -> ASRBackend:
    """获取 ASR 后端实例

    Args:
        backend_type: 后端类型 ("pytorch", "onnx", "sensevoice", "gguf", "qwen3", "vibevoice", "router", "whisper")
        device: 设备类型 ("cuda" 或 "cpu")
        ngpu: GPU 数量
        ncpu: CPU 核心数
        **kwargs: 传递给后端的其他参数

    Returns:
        ASRBackend 实例

    Raises:
        ValueError: 不支持的后端类型
        ImportError: 缺少所需依赖
    """
    if backend_type == "pytorch":
        from .pytorch import PyTorchBackend
        return PyTorchBackend(
            device=device,
            ngpu=ngpu,
            ncpu=ncpu,
            **kwargs
        )

    elif backend_type == "onnx":
        try:
            from .onnx import ONNXBackend
        except ImportError as e:
            raise ImportError(
                "ONNX 后端需要安装 funasr-onnx: pip install funasr-onnx"
            ) from e
        return ONNXBackend(
            device=device,
            ncpu=ncpu,
            **kwargs
        )

    elif backend_type == "sensevoice":
        from .sensevoice import SenseVoiceBackend
        return SenseVoiceBackend(
            device=device,
            ngpu=ngpu,
            ncpu=ncpu,
            **kwargs
        )

    elif backend_type == "gguf":
        try:
            from .gguf import GGUFBackend
        except ImportError as e:
            raise ImportError(
                "GGUF 后端需要安装 onnxruntime 和 gguf: pip install onnxruntime gguf"
            ) from e
        return GGUFBackend(**kwargs)

    elif backend_type == "qwen3":
        from .qwen3_remote import Qwen3RemoteBackend
        return Qwen3RemoteBackend(**kwargs)

    elif backend_type == "vibevoice":
        from .vibevoice_remote import VibeVoiceRemoteBackend
        return VibeVoiceRemoteBackend(**kwargs)

    elif backend_type == "router":
        from .router import RouterBackend
        return RouterBackend(**kwargs)

    elif backend_type == "whisper":
        from .whisper import WhisperBackend
        # Keep behavior consistent with other backends: honor the shared `device`
        # argument (set via Settings.DEVICE / docker compose `DEVICE=...`).
        return WhisperBackend(device=device, **kwargs)

    else:
        raise ValueError(f"不支持的后端类型: {backend_type}")


__all__ = [
    "ASRBackend",
    "BackendType",
    "get_backend",
]
