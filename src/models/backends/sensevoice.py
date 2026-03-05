"""SenseVoice 后端 - 高速语音识别"""
import logging
from typing import Optional, Dict, Any, List

from .base import ASRBackend

logger = logging.getLogger(__name__)

# SenseVoice 模型 ID
SENSEVOICE_MODEL_SMALL = "iic/SenseVoiceSmall"


class SenseVoiceBackend(ASRBackend):
    """基于 SenseVoice 的高速后端

    特点：
    - 极速推理：10秒音频仅需 70ms（比 Whisper-Large 快 15 倍）
    - 多语言支持：50+ 种语言
    - 额外功能：情感识别、音频事件检测
    - 非自回归架构，延迟极低

    Requirements:
        pip install funasr>=1.1.0
    """

    def __init__(
        self,
        device: str = "cuda",
        ngpu: int = 1,
        ncpu: int = 4,
        model: str = SENSEVOICE_MODEL_SMALL,
        language: str = "zh",
        **kwargs
    ):
        """初始化 SenseVoice 后端

        Args:
            device: 设备类型 ("cuda" 或 "cpu")
            ngpu: GPU 数量
            ncpu: CPU 线程数
            model: 模型 ID
            language: 默认语言 ("zh", "en", "ja", "ko", "yue" 等)
        """
        self.device = device
        self.ngpu = ngpu
        self.ncpu = ncpu
        self.model_name = model
        self.language = language

        self._model = None
        self._loaded = False

    def load(self) -> None:
        """加载 SenseVoice 模型"""
        if self._loaded:
            return

        try:
            from funasr import AutoModel
        except ImportError:
            raise ImportError(
                "SenseVoice 后端需要安装 funasr>=1.1.0: pip install funasr"
            )

        logger.info(f"Loading SenseVoice model: {self.model_name}")
        logger.info(f"Device: {self.device}, Language: {self.language}")

        self._model = AutoModel(
            model=self.model_name,
            trust_remote_code=True,
            device=self.device,
            ngpu=self.ngpu,
            ncpu=self.ncpu,
            disable_pbar=True,
            disable_log=True,
        )

        self._loaded = True
        logger.info("SenseVoice backend loaded successfully")

    def _ensure_loaded(self):
        """确保模型已加载"""
        if not self._loaded:
            self.load()

    @property
    def supports_streaming(self) -> bool:
        """SenseVoice 不支持流式"""
        return False

    @property
    def supports_speaker(self) -> bool:
        """SenseVoice 不支持说话人识别"""
        return False

    @property
    def supports_batch(self) -> bool:
        # FunASR AutoModel.generate supports list inputs and performs padded batching internally.
        return True

    def transcribe(
        self,
        audio_input,
        hotwords: Optional[str] = None,
        language: Optional[str] = None,
        with_speaker: bool = False,
        use_emotion: bool = False,
        use_event: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """执行转写

        Args:
            audio_input: 音频输入（文件路径或字节）
            hotwords: 热词（SenseVoice 后端需通过后处理纠正）
            language: 语言代码（覆盖默认语言）
            with_speaker: 忽略（SenseVoice 不支持）
            use_emotion: 是否返回情感信息
            use_event: 是否返回音频事件信息
            **kwargs: 其他参数

        Returns:
            转写结果字典，包含 text 和 sentence_info
        """
        self._ensure_loaded()

        if with_speaker:
            logger.warning("SenseVoice backend does not support speaker diarization")

        if hotwords:
            logger.debug("SenseVoice backend: hotwords will be processed via post-processing pipeline")

        lang = language or self.language

        try:
            result = self._model.generate(
                input=audio_input,
                language=lang,
                use_itn=True,  # 启用逆文本规范化
                **kwargs
            )

            if result and len(result) > 0:
                item = result[0]
                raw_text = item.get("text", "")

                # 清理 SenseVoice 特殊标签 (如 <|zh|><|HAPPY|><|Speech|><|woitn|>)
                text = self._clean_sensevoice_tags(raw_text)

                # 提取情感和事件信息（从标签解析）
                emotion, event = self._parse_sensevoice_tags(raw_text)

                # 构建返回结果
                response = {
                    "text": text,
                    "sentence_info": [{"text": text, "start": 0, "end": 0}] if text else [],
                }

                # 添加情感信息
                if use_emotion and emotion:
                    response["emotion"] = emotion

                # 添加事件信息
                if use_event and event:
                    response["event"] = event

                return response

            return {"text": "", "sentence_info": []}

        except Exception as e:
            logger.error(f"SenseVoice transcription failed: {e}")
            raise

    def transcribe_batch(
        self,
        audio_inputs: List[Any],
        hotwords: Optional[str] = None,
        language: Optional[str] = None,
        with_speaker: bool = False,
        use_emotion: bool = False,
        use_event: bool = False,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """批量转写（优先走 FunASR 内部 padded batching）。"""
        self._ensure_loaded()
        items = list(audio_inputs or [])
        if not items:
            return []

        if with_speaker:
            logger.warning("SenseVoice backend does not support speaker diarization (batch); ignoring with_speaker=True")

        if hotwords:
            logger.debug("SenseVoice backend (batch): hotwords will be processed via post-processing pipeline")

        lang = language or self.language

        try:
            result = self._model.generate(  # type: ignore[union-attr]
                input=items,
                language=lang,
                use_itn=True,
                **kwargs,
            )
        except Exception as e:
            logger.warning(f"SenseVoice batch generate failed, falling back to per-item: {e}")
            return [
                self.transcribe(
                    x,
                    hotwords=hotwords,
                    language=language,
                    with_speaker=False,
                    use_emotion=use_emotion,
                    use_event=use_event,
                    **kwargs,
                )
                for x in items
            ]

        if not isinstance(result, list):
            result = [result] if result is not None else []

        out: List[Dict[str, Any]] = []
        for i in range(len(items)):
            item = result[i] if i < len(result) else None
            if not isinstance(item, dict):
                out.append({"text": "", "sentence_info": []})
                continue

            raw_text = str(item.get("text", "") or "")
            text = self._clean_sensevoice_tags(raw_text)
            emotion, event = self._parse_sensevoice_tags(raw_text)

            resp: Dict[str, Any] = {
                "text": text,
                "sentence_info": [{"text": text, "start": 0, "end": 0}] if text else [],
            }
            if use_emotion and emotion:
                resp["emotion"] = emotion
            if use_event and event:
                resp["event"] = event
            out.append(resp)

        return out

    def _clean_sensevoice_tags(self, text: str) -> str:
        """清理 SenseVoice 输出中的特殊标签

        SenseVoice 输出格式: <|lang|><|emotion|><|event|><|itn|>text
        """
        import re
        # 移除所有 <|...|> 格式的标签
        cleaned = re.sub(r'<\|[^|]+\|>', '', text)
        return cleaned.strip()

    def _parse_sensevoice_tags(self, text: str) -> tuple:
        """从 SenseVoice 输出中解析情感和事件标签

        Returns:
            (emotion, event) 元组
        """
        import re
        emotion = None
        event = None

        # 提取所有标签
        tags = re.findall(r'<\|([^|]+)\|>', text)

        # 已知的情感标签
        emotions = {"HAPPY", "SAD", "ANGRY", "NEUTRAL", "SURPRISED", "FEARFUL", "DISGUSTED"}
        # 已知的事件标签
        events = {"Speech", "Music", "Noise", "Laughter", "Applause"}

        for tag in tags:
            if tag in emotions:
                emotion = tag.lower()
            elif tag in events:
                event = tag.lower()

        return emotion, event

    def transcribe_with_details(
        self,
        audio_input,
        language: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """带详细信息的转写

        返回完整的 SenseVoice 输出，包括情感和事件检测。

        Args:
            audio_input: 音频输入
            language: 语言代码
            **kwargs: 其他参数

        Returns:
            包含 text, emotion, event 的字典
        """
        return self.transcribe(
            audio_input,
            language=language,
            use_emotion=True,
            use_event=True,
            **kwargs
        )

    def unload(self) -> None:
        """释放模型资源"""
        self._model = None
        self._loaded = False
        logger.info("SenseVoice backend models unloaded")

    def get_info(self) -> Dict[str, Any]:
        """获取后端信息"""
        return {
            "name": "SenseVoiceBackend",
            "type": "sensevoice",
            "device": self.device,
            "model": self.model_name,
            "language": self.language,
            "supports_streaming": False,
            "supports_hotwords": True,  # 通过后处理支持
            "supports_speaker": False,
            "supports_emotion": True,
            "supports_event": True,
        }
