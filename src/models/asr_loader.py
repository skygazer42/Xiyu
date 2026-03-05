"""FunASR 模型加载器 - 向后兼容封装
"""
import logging
from typing import Optional, Dict, Any, List

from src.models.backends.pytorch import PyTorchBackend

logger = logging.getLogger(__name__)


class ASRModelLoader:
    """ASR 模型加载器 - 向后兼容封装

    此类封装了 PyTorchBackend，保持与现有代码的兼容性。
    新代码应直接使用 backends 模块。
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
        self._backend = PyTorchBackend(
            device=device,
            ngpu=ngpu,
            ncpu=ncpu,
            asr_model=asr_model,
            asr_model_online=asr_model_online,
            vad_model=vad_model,
            punc_model=punc_model,
            spk_model=spk_model,
        )

    @property
    def asr_model(self):
        """获取离线 ASR 模型"""
        return self._backend.asr_model

    @property
    def asr_model_online(self):
        """获取在线流式 ASR 模型"""
        return self._backend.asr_model_online

    @property
    def asr_model_with_spk(self):
        """获取带说话人识别的 ASR 模型"""
        return self._backend.asr_model_with_spk

    def transcribe(
        self,
        audio_input,
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """执行转写"""
        return self._backend.transcribe(
            audio_input,
            hotwords=hotwords,
            with_speaker=with_speaker,
            **kwargs
        )

    def transcribe_batch(
        self,
        audio_inputs: List[Any],
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """批量转写（向后兼容封装）。"""
        return self._backend.transcribe_batch(
            audio_inputs,
            hotwords=hotwords,
            with_speaker=with_speaker,
            **kwargs,
        )
