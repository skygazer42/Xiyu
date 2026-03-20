"""API 请求/响应模式"""
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class SentenceInfo(BaseModel):
    """句子信息"""
    text: str = Field(..., description="句子文本")
    start: int = Field(..., description="开始时间 (毫秒)")
    end: int = Field(..., description="结束时间 (毫秒)")
    speaker: Optional[str] = Field(default=None, description="说话人标签")
    speaker_id: Optional[int] = Field(default=None, description="说话人 ID")


class WordInfo(BaseModel):
    """词/Token 时间戳（best-effort，对齐输出）"""

    text: str = Field(..., description="词/Token 文本")
    start: int = Field(..., description="开始时间 (毫秒)")
    end: int = Field(..., description="结束时间 (毫秒)")


class SpeakerTurn(BaseModel):
    """说话人 turn/段落（合并后的说话人连续发言）"""
    speaker: str = Field(..., description="说话人标签")
    speaker_id: int = Field(..., description="说话人 ID")
    start: int = Field(..., description="开始时间 (毫秒)")
    end: int = Field(..., description="结束时间 (毫秒)")
    text: str = Field(..., description="该 turn 的文本")
    sentence_count: int = Field(default=1, description="包含句子数")


class TranscribeResponse(BaseModel):
    """转写响应"""
    code: int = Field(default=0, description="状态码 (0=成功)")
    text: str = Field(..., description="完整转写文本")
    text_accu: Optional[str] = Field(
        default=None,
        description="精确拼接文本（长音频分块去重更严格，适合回忆/会议转录）",
    )
    sentences: List[SentenceInfo] = Field(default=[], description="分句信息")
    speaker_turns: Optional[List[SpeakerTurn]] = Field(default=None, description="说话人 turn/段落")
    transcript: Optional[str] = Field(default=None, description="格式化转写稿")
    words: Optional[List[WordInfo]] = Field(
        default=None,
        description=(
            "词/Token 级时间戳（可选，best-effort）。"
            "仅在 asr_options.alignment.enable=true 时返回；与 text_accu 对齐（当开启对齐时 text_accu 会被冻结以保证一致性）。"
        ),
    )
    srt: Optional[str] = Field(
        default=None,
        description="SRT 字幕内容（可选，基于 sentences 或 speaker_turns 生成，包含说话人标签）",
    )
    raw_text: Optional[str] = Field(default=None, description="原始文本 (未纠错)")
    overview: Optional[str] = Field(
        default=None,
        description="会议概览（可选，LLM 生成；2-5 段政务口径概览文本）",
    )
    overview_task_id: Optional[str] = Field(
        default=None,
        description="会议概览异步任务 ID（可选；可通过 /api/v1/result 轮询获取 overview）",
    )


class BatchTranscribeItem(BaseModel):
    """批量转写结果项"""
    index: int = Field(..., description="文件索引")
    filename: str = Field(..., description="文件名")
    success: bool = Field(default=True, description="是否成功")
    result: Optional[TranscribeResponse] = Field(default=None, description="转写结果")
    error: Optional[str] = Field(default=None, description="错误信息")


class BatchTranscribeResponse(BaseModel):
    """批量转写响应"""
    code: int = Field(default=0, description="状态码")
    total: int = Field(..., description="总文件数")
    success_count: int = Field(..., description="成功数")
    failed_count: int = Field(..., description="失败数")
    results: List[BatchTranscribeItem] = Field(default=[], description="各文件结果")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(..., description="服务状态")
    version: str = Field(..., description="服务版本")


class MetricsResponse(BaseModel):
    """指标响应"""
    uptime_seconds: float = Field(..., description="服务运行时间")
    total_requests: int = Field(..., description="总请求数")
    successful_requests: int = Field(..., description="成功请求数")
    failed_requests: int = Field(..., description="失败请求数")
    total_audio_seconds: float = Field(..., description="处理音频总时长")
    avg_rtf: float = Field(..., description="平均实时因子")
    llm_cache_stats: Dict[str, Any] = Field(default={}, description="LLM 缓存统计")


class BackendCapabilities(BaseModel):
    """后端能力声明（用于前端探测/提示）"""
    supports_speaker: bool = Field(..., description="是否支持说话人识别/分离输出")
    supports_streaming: bool = Field(..., description="是否支持流式转写")
    supports_hotwords: bool = Field(..., description="是否支持热词（注入或后处理）")
    supports_speaker_fallback: bool = Field(
        default=False,
        description="是否启用/可用说话人 fallback（当后端原生不支持 speaker 时，通过辅助服务生成说话人段落）",
    )
    supports_speaker_external: bool = Field(
        default=False,
        description="是否启用/可用 external diarizer（外部说话人分离服务）",
    )
    speaker_strategy: Literal[
        "external",
        "native",
        "fallback_diarization",
        "fallback_backend",
        "ignore",
        "error",
    ] = Field(
        ...,
        description="当 with_speaker=true 时的实际说话人策略（考虑当前配置）",
    )


class BackendInfoResponse(BaseModel):
    """后端信息（用于多端口/多模型部署场景的能力探测）"""
    backend: str = Field(..., description="配置的后端类型（ASR_BACKEND）")
    info: Dict[str, Any] = Field(default_factory=dict, description="backend.get_info() 输出（安全元信息）")
    capabilities: BackendCapabilities = Field(..., description="后端能力")
    speaker_unsupported_behavior: Literal["error", "fallback", "ignore"] = Field(
        ...,
        description="当 with_speaker=true 但后端不支持时的行为",
    )


class BackendTargetStatus(BaseModel):
    """Router backend target probe status."""

    key: str = Field(..., description="目标后端 key（如 qwen3/vibevoice/pytorch/onnx/...）")
    ok: bool = Field(..., description="是否可用（探测成功）")
    info: Dict[str, Any] = Field(default_factory=dict, description="backend.get_info() 元信息（尽量安全/小）")
    error: Optional[str] = Field(default=None, description="失败原因（可选）")


class BackendTargetsResponse(BaseModel):
    """Backend target list (router-only)."""

    code: int = Field(default=0, description="状态码 (0=成功)")
    backend: str = Field(..., description="当前实例的 ASR_BACKEND")
    targets: List[BackendTargetStatus] = Field(default_factory=list, description="Router 可选 target 列表")


class EnsembleCandidate(BaseModel):
    """多模型转写候选（用于全量/ensemble 接口）"""

    backend: str = Field(..., description="候选后端名（pytorch/onnx/...）")
    base_url: str = Field(..., description="候选后端 base url（容器内可用）")
    success: bool = Field(default=True, description="是否成功")
    http_status: Optional[int] = Field(default=None, description="HTTP 状态码（如可用）")
    code: Optional[int] = Field(default=None, description="Xiyu code 字段（如可用）")
    elapsed_ms: Optional[int] = Field(default=None, description="耗时（毫秒）")
    text: Optional[str] = Field(default=None, description="候选文本（可能截断）")
    cleaned_text: Optional[str] = Field(default=None, description="用于 LLM 参考的清洗文本（可能截断）")
    error: Optional[str] = Field(default=None, description="错误信息（失败时）")


class EnsembleTranscribeResponse(BaseModel):
    """多模型全量转写 + LLM 融合响应"""

    code: int = Field(default=0, description="状态码 (0=成功)")
    base_backend: str = Field(..., description="用作说话人骨架的后端（通常 pytorch）")
    llm_used: bool = Field(default=True, description="是否实际调用了 LLM")
    llm_role: str = Field(default="policy_meeting", description="LLM 角色")
    candidates: List[EnsembleCandidate] = Field(default=[], description="各后端候选结果（用于对比/追踪）")
    final: TranscribeResponse = Field(..., description="最终融合后的转写结果")

