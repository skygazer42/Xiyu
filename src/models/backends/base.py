"""ASR 后端抽象基类"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


class ASRBackend(ABC):
    """ASR 后端抽象基类

    所有 ASR 后端（PyTorch、ONNX、SenseVoice 等）都需要实现此接口。
    """

    @abstractmethod
    def load(self) -> None:
        """加载模型到内存

        子类必须实现此方法来初始化模型。
        应支持懒加载模式。
        """
        pass

    @abstractmethod
    def transcribe(
        self,
        audio_input,
        hotwords: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """执行转写

        Args:
            audio_input: 音频输入，可以是文件路径、字节或 numpy 数组
            hotwords: 热词字符串（空格分隔）
            **kwargs: 其他参数

        Returns:
            转写结果字典，包含:
            - text: 完整转写文本
            - sentence_info: 句子级别信息列表（可选）
        """
        pass

    @property
    def supports_streaming(self) -> bool:
        """是否支持流式转写"""
        return False

    @property
    def supports_hotwords(self) -> bool:
        """是否支持热词"""
        return True

    @property
    def supports_speaker(self) -> bool:
        """是否支持说话人识别"""
        return False

    @property
    def supports_batch(self) -> bool:
        """是否支持 batch 推理（服务端一次调用处理多个音频）

        注意：即使 supports_batch=False，仍然可以调用 transcribe_batch，
        只是会退化为逐条 transcribe()。
        """
        return False

    def transcribe_batch(
        self,
        audio_inputs: List[Any],
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """批量转写（best-effort）

        默认实现：逐条调用 transcribe()。
        对于支持 padded batching 的后端（例如 FunASR AutoModel.generate），
        子类应覆盖此方法以获得更高吞吐与更稳定的并发行为。

        Args:
            audio_inputs: 多个音频输入（bytes/path/numpy 等）
            hotwords: 热词字符串（统一应用到 batch 内所有样本）
            with_speaker: 是否启用说话人识别（统一应用）
            **kwargs: 其他参数（统一应用）

        Returns:
            每条音频对应一个转写结果 dict
        """
        results: List[Dict[str, Any]] = []
        for audio_input in audio_inputs:
            results.append(
                self.transcribe(
                    audio_input,
                    hotwords=hotwords,
                    with_speaker=with_speaker,
                    **kwargs,
                )
            )
        return results

    def transcribe_streaming(
        self,
        audio_chunk: bytes,
        cache: Dict[str, Any],
        is_final: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """流式转写（单个音频块）

        Args:
            audio_chunk: 音频数据块
            cache: 状态缓存字典
            is_final: 是否为最后一个块
            **kwargs: 其他参数

        Returns:
            转写结果字典
        """
        raise NotImplementedError("此后端不支持流式转写")

    def unload(self) -> None:
        """卸载模型，释放资源

        子类可以覆盖此方法来清理资源。
        """
        pass

    def get_info(self) -> Dict[str, Any]:
        """获取后端信息

        Returns:
            包含后端名称、版本、能力等信息的字典
        """
        return {
            "name": self.__class__.__name__,
            "supports_streaming": self.supports_streaming,
            "supports_hotwords": self.supports_hotwords,
            "supports_speaker": self.supports_speaker,
            "supports_batch": self.supports_batch,
        }
