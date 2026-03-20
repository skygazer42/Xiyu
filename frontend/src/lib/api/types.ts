// API 类型定义 - 基于后端 src/api/schemas.py

export interface SentenceInfo {
  text: string
  start: number  // 毫秒
  end: number    // 毫秒
  speaker?: string
  speaker_id?: number
}

export interface SpeakerTurn {
  speaker: string
  speaker_id: number
  start: number  // 毫秒
  end: number    // 毫秒
  text: string
  sentence_count: number
}

export interface TranscribeResponse {
  code: number
  text: string
  text_accu?: string | null
  sentences: SentenceInfo[]
  speaker_turns?: SpeakerTurn[] | null
  transcript?: string
  /** Backend-generated SRT content (optional). */
  srt?: string | null
  raw_text?: string
  /** Meeting overview text (optional, LLM-generated). */
  overview?: string | null
  /** Meeting overview async task id (optional; poll via /api/v1/result). */
  overview_task_id?: string | null
}

export interface BatchTranscribeItem {
  index: number
  filename: string
  success: boolean
  result?: TranscribeResponse
  error?: string
}

export interface BatchTranscribeResponse {
  code: number
  total: number
  success_count: number
  failed_count: number
  results: BatchTranscribeItem[]
}

// URL 转写相关
export interface UrlTranscribeRequest {
  audio_url: string
  with_speaker?: boolean
  apply_hotword?: boolean
  apply_llm?: boolean
  llm_role?: string
  /** Router 目标后端（单端口部署专用）：auto/qwen3/vibevoice/pytorch/onnx/sensevoice/gguf/whisper... */
  target_backend?: string
  hotwords?: string
  asr_options?: string
}

export interface AsyncTaskSubmitResponse {
  code: number
  status: 'success' | 'error'
  message: string
  data?: {
    task_id: string
  }
}

export type UrlTranscribeResponse = AsyncTaskSubmitResponse

export type FileTranscribeResponse = AsyncTaskSubmitResponse

// 异步任务相关
export interface TaskResultRequest {
  task_id: string
  delete?: boolean
}

export interface TaskResultResponse {
  code: number
  status: 'pending' | 'processing' | 'success' | 'error'
  message: string
  data?:
    | { task_id: string; progress?: number | null; detail?: string | null }
    | TranscribeResponse
}

// 视频转写相关
export interface VideoTranscribeResponse extends TranscribeResponse {
  video_duration?: number
  audio_extracted?: boolean
}

// Whisper 兼容接口（/api/v1/asr）
export interface WhisperAsrSegment {
  sentence_index: number
  text: string
  /** HH:MM:SS.mmm */
  start: string
  /** HH:MM:SS.mmm */
  end: string
  speaker?: string
}

export interface WhisperAsrResponse {
  text: string
  segments: WhisperAsrSegment[]
  language: string
}

export interface HealthResponse {
  status: string
  version: string
}

export interface MetricsResponse {
  uptime_seconds: number
  total_requests: number
  successful_requests: number
  failed_requests: number
  total_audio_seconds: number
  avg_rtf: number
  llm_cache_stats: Record<string, unknown>
}

export interface BackendCapabilities {
  supports_speaker: boolean
  supports_streaming: boolean
  supports_hotwords: boolean
  supports_speaker_fallback: boolean
  supports_speaker_external: boolean
  speaker_strategy:
    | 'external'
    | 'native'
    | 'fallback_diarization'
    | 'fallback_backend'
    | 'ignore'
    | 'error'
}

export interface BackendInfoResponse {
  backend: string
  info: Record<string, unknown>
  capabilities: BackendCapabilities
  speaker_unsupported_behavior: 'error' | 'fallback' | 'ignore'
}

export interface BackendTargetStatus {
  key: string
  ok: boolean
  info?: Record<string, unknown>
  error?: string | null
}

export interface BackendTargetsResponse {
  code: number
  backend: string
  targets: BackendTargetStatus[]
}

// 全量/Ensemble 接口
export interface EnsembleCandidate {
  backend: string
  base_url: string
  success: boolean
  http_status?: number | null
  code?: number | null
  elapsed_ms?: number | null
  text?: string | null
  cleaned_text?: string | null
  error?: string | null
}

export interface EnsembleTranscribeResponse {
  code: number
  base_backend: string
  llm_used: boolean
  llm_role: string
  candidates: EnsembleCandidate[]
  final: TranscribeResponse
}

// 热词相关
export interface HotwordsListResponse {
  code: number
  hotwords: string[]
  count: number
}

export interface HotwordsUpdateRequest {
  hotwords: string[]
}

export interface HotwordsUpdateResponse {
  code: number
  count: number
  message: string
}

// 热词文件（rules / rectify）
export interface TextFileResponse {
  code: number
  text: string
  count: number
  message: string
}

export interface TextFileUpdateRequest {
  text: string
}

export interface RectifyAppendRequest {
  wrong: string
  right: string
}

// 配置相关
export interface ConfigResponse {
  config: Record<string, unknown>
}

export interface ConfigAllResponse {
  config: Record<string, unknown>
  mutable_keys: string[]
}

export interface ConfigUpdateRequest {
  updates: Record<string, unknown>
}

// 转写请求选项
export type SingleLlmRole =
  | 'default'
  | 'meeting'
  | 'corrector'
  | 'translator'
  | 'code'
  | 'policy_polish_strict'
  | 'policy_polish_balanced'
  | 'policy_polish_aggressive'

export type EnsembleLlmRole = 'policy_meeting' | 'policy_meeting_v2' | 'policy_meeting_aggressive'

export interface TranscribeOptions {
  with_speaker?: boolean
  apply_hotword?: boolean
  apply_llm?: boolean
  llm_role?: SingleLlmRole
  hotwords?: string
  speaker_label_style?: 'numeric' | 'zh'
  /** Router 目标后端（单端口部署专用）：auto/qwen3/vibevoice/pytorch/onnx/sensevoice/gguf/whisper... */
  target_backend?: string
}

// WebSocket 消息类型
export interface WSConnectedMessage {
  type: 'connected'
  connection_id: string
  config: {
    chunk_size: number
    heartbeat_interval: number
    compression: boolean
  }
}

export interface WSResultMessage {
  mode: '2pass-online' | '2pass-offline' | 'online' | 'offline'
  text: string
  is_final: boolean
}

export interface WSPingMessage {
  type: 'ping'
  timestamp: number
}

export interface WSWarningMessage {
  warning: string
  backend: string
}

export type WSMessage = WSConnectedMessage | WSResultMessage | WSPingMessage | WSWarningMessage
