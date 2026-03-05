import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, Literal

class Settings(BaseSettings):
    """应用配置"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Repo root `.env` is shared with docker-compose and may contain many
        # non-runtime keys (PORT_* etc). Ignore unknown keys instead of failing
        # on import/tests.
        extra="ignore",
    )
    # 服务配置
    app_name: str = "Xiyu Speech Service"
    version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # 路径配置
    base_dir: Path = Path(__file__).parent.parent
    data_dir: Path = base_dir / "data"
    models_dir: Path = data_dir / "models"
    hotwords_dir: Path = data_dir / "hotwords"
    uploads_dir: Path = data_dir / "uploads"
    outputs_dir: Path = data_dir / "outputs"

    # ASR 后端配置
    asr_backend: Literal[
        "pytorch",
        "onnx",
        "sensevoice",
        "gguf",
        "qwen3",
        "vibevoice",
        "router",
        "whisper",
    ] = "pytorch"

    # FunASR 模型配置 (PyTorch 后端)
    asr_model: str = "paraformer-zh"
    asr_model_online: str = "paraformer-zh-streaming"
    vad_model: str = "fsmn-vad"
    punc_model: str = "ct-punc-c"
    spk_model: str = "cam++"

    # VAD 参数优化
    vad_max_segment_ms: int = 60000              # VAD 单段最大时长 (毫秒)
    vad_speech_noise_thres: float = 0.8          # 语音/噪声阈值

    # ONNX 后端配置
    onnx_quantize: bool = True  # 启用 INT8 量化
    onnx_intra_threads: int = 4  # ONNX 推理线程数
    onnx_inter_threads: int = 1  # ONNX 并行操作数

    # 模型预热配置
    warmup_on_startup: bool = True  # 启动时预热模型
    warmup_audio_duration: float = 1.0  # 预热音频时长(秒)

    # SenseVoice 后端配置
    sensevoice_model: str = "iic/SenseVoiceSmall"
    sensevoice_language: str = "zh"

    # ------------------------------------------------------------
    # Speaker / 会议转写输出配置
    # ------------------------------------------------------------
    # 说话人标签风格: zh=说话人甲/乙/丙, numeric=说话人1/2/3
    speaker_label_style: Literal["zh", "numeric"] = "zh"
    # 合并同一说话人的连续句子为“turn/段落”
    speaker_turn_merge_enable: bool = True
    # 两句之间间隔小于该值 (ms) 则合并为同一 turn
    speaker_turn_merge_gap_ms: int = 800
    # turn 最少字符数（避免生成特别短的碎片段落）
    speaker_turn_merge_min_chars: int = 1
    # 后端不支持说话人识别时是否严格报错（避免静默回退到其它模型）
    speaker_strict_backend: bool = True
    # 新版：后端不支持说话人识别时的行为控制
    # - error: 直接报错（HTTP 400）
    # - fallback: 回退到 PyTorch 后端（可能违反“端口=模型”的预期）
    # - ignore: 忽略说话人（按 with_speaker=false 处理，适合多端口部署）
    speaker_unsupported_behavior: Optional[Literal["error", "fallback", "ignore"]] = None

    # ------------------------------------------------------------
    # Speaker fallback diarization (辅助说话人分离)
    # ------------------------------------------------------------
    # 当当前后端不支持说话人识别（例如 Qwen3-ASR），但用户请求 with_speaker=true 时：
    # - 可选：调用一个“辅助 Xiyu 服务”（通常是 xiyu-pytorch）获取说话人分段
    # - 然后按说话人 turn 切片用当前后端转写，从而输出说话人1/2/3...
    # 失败会自动回退到 speaker_unsupported_behavior 的逻辑（推荐 ignore）。
    speaker_fallback_diarization_enable: bool = False
    speaker_fallback_diarization_base_url: str = ""
    speaker_fallback_diarization_timeout_s: float = 30.0
    speaker_fallback_max_turn_duration_s: float = 25.0
    speaker_fallback_max_turns: int = 200

    # ------------------------------------------------------------
    # Speaker external diarizer (外部说话人分离服务)
    # ------------------------------------------------------------
    speaker_external_diarizer_enable: bool = False
    speaker_external_diarizer_base_url: str = ""
    speaker_external_diarizer_timeout_s: float = 30.0
    speaker_external_diarizer_max_turn_duration_s: float = 25.0
    speaker_external_diarizer_max_turns: int = 200

    # GGUF 后端配置 (FunASR-Nano-GGUF)
    # Default layout (recommended):
    #   data/models/Fun-ASR-Nano-GGUF/
    #     Fun-ASR-Nano-Encoder-Adaptor.fp16.onnx
    #     Fun-ASR-Nano-CTC.int8.onnx
    #     Fun-ASR-Nano-Decoder.q8_0.gguf
    #     tokens.txt
    #
    # Notes:
    # - Older guides placed these files directly under data/models/.
    # - The GGUF backend includes runtime fallback resolution to support both layouts.
    # GGUF is a CPU-oriented backend. Prefer the int8 encoder adaptor to avoid
    # float16 CPU numerical issues (some fp16 exported graphs can output NaNs
    # on CPU providers).
    gguf_encoder_path: str = "models/Fun-ASR-Nano-GGUF/Fun-ASR-Nano-Encoder-Adaptor.int8.onnx"
    gguf_ctc_path: str = "models/Fun-ASR-Nano-GGUF/Fun-ASR-Nano-CTC.int8.onnx"
    gguf_decoder_path: str = "models/Fun-ASR-Nano-GGUF/Fun-ASR-Nano-Decoder.q8_0.gguf"
    gguf_tokens_path: str = "models/Fun-ASR-Nano-GGUF/tokens.txt"
    gguf_lib_dir: str = "models/bin"  # llama.cpp 库目录
    # GGUF llama.cpp context configuration (prompt injection tokens limit).
    # If you hit hard llama.cpp asserts like:
    #   GGML_ASSERT(n_tokens_all <= cparams.n_batch)
    # increase these values, or reduce chunk duration (VAD / diarizer max turn).
    gguf_n_ctx: int = 2048
    gguf_n_batch: int = 2048
    # Some GGUF ONNX exports have shape assumptions that break on very short
    # chunks (e.g. GatherND index out of range). Pad short audio up to this
    # minimum sample count (16kHz mono) before running encoder/CTC.
    gguf_min_samples: int = 96000  # ~= 6.0s @ 16kHz

    # Remote ASR 后端配置（自建 OpenAI-compatible server）
    # Qwen3-ASR: 优先 /v1/audio/transcriptions（Whisper 风格），部分部署才支持 /v1/chat/completions（audio_url）。
    qwen3_asr_base_url: str = "http://localhost:9001"
    qwen3_asr_model: str = "Qwen/Qwen3-ASR-0.6B"
    qwen3_asr_api_key: str = "EMPTY"
    qwen3_asr_timeout_s: float = 60.0

    # VibeVoice-ASR: /v1/chat/completions (audio_url), returns JSON segments with timestamps + speaker id
    vibevoice_asr_base_url: str = "http://localhost:9002"
    vibevoice_asr_model: str = "vibevoice"
    vibevoice_asr_api_key: str = "EMPTY"
    vibevoice_asr_timeout_s: float = 600.0
    vibevoice_asr_use_chat_completions_fallback: bool = True
    # NOTE: used by docker-compose.remote-asr.yml for vLLM container tuning.
    # Keep in Settings so extra env vars in local .env won't break app import/tests.
    vibevoice_gpu_memory_utilization: Optional[float] = None

    # Router 后端：根据音频时长/是否需要说话人自动选择后端
    router_long_audio_threshold_s: float = 60.0
    router_force_vibevoice_when_with_speaker: bool = True
    router_short_backend: Literal["qwen3", "vibevoice", "whisper"] = "qwen3"
    router_long_backend: Literal["qwen3", "vibevoice", "whisper"] = "vibevoice"

    # Local Whisper backend (openai-whisper)
    whisper_model: str = "large"
    whisper_language: Optional[str] = "zh"
    # Set to a persistent directory (e.g. /app/data/models/whisper) to cache weights.
    whisper_download_root: str = ""
    # Router-only: prefer proxying to an existing Xiyu whisper service container
    # (avoid loading weights twice).
    whisper_service_base_url: str = ""
    whisper_service_timeout_s: float = 600.0

    # 设备配置
    device: Literal["cuda", "cpu"] = "cuda"
    ngpu: int = 1
    ncpu: int = 4

    # 热词配置
    hotwords_file: str = "hotwords.txt"
    # 仅用于“上下文提示/前向注入”的热词（不强制替换），更适合会议/回忆转录的专有名词列表
    hotwords_context_file: str = "hotwords-context.txt"
    hotwords_threshold: float = 0.85
    hotword_injection_enable: bool = True       # 热词前向注入 (传递给ASR模型)
    hotword_injection_max: int = 50             # 最大注入热词数
    hotword_watch_enable: bool = True           # 热词文件热加载
    hotword_watch_debounce: float = 3.0         # 热加载防抖秒数
    hotword_use_faiss: bool = False             # 使用 FAISS 向量索引 (大规模热词加速)
    hotword_faiss_index_type: str = "IVFFlat"   # FAISS 索引类型 (IVFFlat, HNSW)

    # LLM 优化配置
    llm_enable: bool = False
    llm_model: str = "qwen2.5:7b"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""  # API Key (OpenAI 兼容接口需要)
    llm_backend: str = "auto"  # auto, ollama, openai, vllm
    llm_role: str = "default"  # default, translator, code, corrector
    llm_context_sentences: int = 1  # 上下文句子数 (用于多句润色)
    llm_fulltext_enable: bool = False  # 全文纠错模式
    llm_fulltext_max_chars: int = 2000  # 全文最大字数
    llm_batch_size: int = 5  # 批量润色句子数
    llm_max_tokens: int = 4096  # LLM 上下文 token 限制
    llm_cache_enable: bool = True  # LLM 响应缓存
    llm_cache_size: int = 1000  # 缓存大小
    llm_cache_ttl: int = 3600  # 缓存 TTL (秒)

    # 通用文本纠错配置 (pycorrector)
    text_correct_enable: bool = False            # 通用文本纠错开关
    text_correct_backend: str = "kenlm"          # kenlm | macbert
    text_correct_device: str = "cpu"             # cpu | cuda (仅 macbert)

    # 置信度过滤配置
    confidence_threshold: float = 0.0            # 置信度阈值 (0=禁用)
    confidence_fallback: str = "pycorrector"     # 低置信度回退策略: pycorrector | llm

    # 文本后处理配置
    filler_remove_enable: bool = False         # 填充词移除 (如 "呃"、"那个"、"就是说")
    filler_aggressive: bool = False            # 激进模式移除更多填充词
    qj2bj_enable: bool = True                  # 全角字符归一化 (ＡＢＣＤ → ABCD)
    itn_enable: bool = True                    # 中文数字格式化 (如 "三百五十" → "350")
    itn_erhua_remove: bool = False             # 儿化移除 (如 "那边儿" → "那边")
    spacing_cjk_ascii_enable: bool = False     # 中英文间距 (如 "AI技术" → "AI 技术")
    spoken_punc_enable: bool = False           # 口述标点指令 (如 "逗号/句号/换行")
    acronym_merge_enable: bool = False         # 英文缩写合并 (如 "A I" → "AI")
    zh_convert_enable: bool = False            # 繁简转换
    zh_convert_locale: str = "zh-hans"         # 目标区域: zh-hans/zh-hant/zh-tw/zh-hk
    punc_convert_enable: bool = False          # 标点转换 (全角→半角)
    punc_add_space: bool = True                # 标点后添加空格
    punc_restore_enable: bool = False          # 独立标点恢复 (FunASR ct-punc)
    punc_restore_model: str = "ct-punc-c"      # 标点恢复模型
    punc_merge_enable: bool = False            # 标点智能合并

    # 末尾标点移除 (用于实时转写场景)
    trash_punc_enable: bool = False            # 启用末尾标点移除
    trash_punc_chars: str = "，。,."            # 要移除的标点字符

    # 纠错管线编排 (按顺序执行)
    # 可用步骤: hotword, rules, pycorrector, post_process
    correction_pipeline: str = "hotword,rules,pycorrector,post_process"

    # WebSocket 配置
    ws_chunk_size: int = 9600  # 600ms @ 16kHz
    ws_chunk_interval: int = 10
    ws_compression: bool = True  # 启用 WebSocket 压缩
    ws_heartbeat_interval: int = 30  # 心跳间隔 (秒)
    ws_heartbeat_timeout: int = 60  # 心跳超时 (秒)

    # 异步任务队列（长音频/批量转写）
    # - 对 3-4 小时会议长音频：单个任务可能跑很久，结果也可能较大
    # - 前端刷新后仍希望能继续查询，因此默认保留更久
    task_max_results: int = 200           # 最大缓存任务结果数
    task_result_ttl_s: int = 24 * 3600    # 结果保留时间（秒）

    # 音频预处理配置
    audio_normalize_enable: bool = True          # 音量归一化
    audio_normalize_target_db: float = -20.0     # 目标电平 (dB)
    audio_trim_silence_enable: bool = False      # 静音裁剪
    audio_silence_threshold_db: float = -40.0    # 静音阈值 (dB)
    audio_denoise_enable: bool = False           # 降噪开关
    audio_denoise_prop: float = 0.8              # 降噪强度 (0-1)
    audio_denoise_backend: str = "noisereduce"   # 降噪后端: noisereduce | deepfilter | deepfilter3
    audio_vocal_separate_enable: bool = False    # 人声分离开关
    audio_vocal_separate_model: str = "htdemucs" # 人声分离模型
    audio_adaptive_preprocess: bool = False      # 自适应预处理 (根据 SNR 智能选择)
    audio_snr_threshold: float = 20.0            # SNR 阈值 (低于此值启用降噪)

    # 流式文本去重配置
    stream_dedup_enable: bool = True             # 启用流式去重
    stream_dedup_overlap: int = 5                # 重叠检查字符数
    stream_dedup_tolerance: int = 1              # 模糊匹配容差

    @property
    def speaker_unsupported_behavior_effective(self) -> Literal["error", "fallback", "ignore"]:
        """Resolve effective unsupported-speaker behavior.

        Prefer the new `speaker_unsupported_behavior` if set, otherwise map legacy
        `speaker_strict_backend` for backward compatibility.
        """
        if self.speaker_unsupported_behavior in ("error", "fallback", "ignore"):
            return self.speaker_unsupported_behavior
        return "error" if self.speaker_strict_backend else "fallback"

settings = Settings()

# 确保目录存在
for dir_path in [settings.data_dir, settings.models_dir, settings.hotwords_dir,
                 settings.uploads_dir, settings.outputs_dir]:
    dir_path.mkdir(parents=True, exist_ok=True)
