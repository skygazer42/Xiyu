import apiClient from './client'
import type {
  TranscribeResponse,
  BatchTranscribeResponse,
  TranscribeOptions,
  EnsembleTranscribeResponse,
  UrlTranscribeResponse,
  TaskResultResponse,
  VideoTranscribeResponse,
  WhisperAsrResponse,
} from './types'

export interface UploadProgressCallback {
  (progress: number): void
}

export interface TranscribeWithProgressOptions extends TranscribeOptions {
  onUploadProgress?: UploadProgressCallback
  signal?: AbortSignal
  /**
   * Advanced per-request ASR tuning options (`asr_options`) as a JSON string.
   * This is merged with UI-derived options like speaker label style.
   */
  asrOptionsText?: string
}

export interface UrlTranscribeOptions extends TranscribeOptions {
  signal?: AbortSignal
  /**
   * Advanced per-request ASR tuning options (`asr_options`) as a JSON string.
   * This is merged with UI-derived options like speaker label style.
   */
  asrOptionsText?: string
}

export interface ApiRequestOverrides {
  /**
   * Override `baseURL` per request so async tasks keep polling the backend they
   * were created on (even if the user switches the global backend later).
   */
  baseURL?: string
}

function parseAsrOptionsText(text: string | undefined): Record<string, unknown> {
  const s = (text || '').trim()
  if (!s) {
    return {}
  }

  const obj: unknown = JSON.parse(s)
  if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
    throw new Error('高级 asr_options 必须是 JSON 对象')
  }
  return obj as Record<string, unknown>
}

function mergeSpeakerLabelStyle(
  base: Record<string, unknown>,
  labelStyle: 'numeric' | 'zh'
): Record<string, unknown> {
  const existing = base.speaker
  const speakerSection: Record<string, unknown> =
    existing && typeof existing === 'object' && !Array.isArray(existing)
      ? { ...(existing as Record<string, unknown>) }
      : {}

  speakerSection.label_style = labelStyle
  return { ...base, speaker: speakerSection }
}

/**
 * 单文件转写 (带上传进度)
 */
export async function transcribeAudio(
  file: File,
  options: TranscribeWithProgressOptions = {}
): Promise<TranscribeResponse> {
  const { onUploadProgress, signal, asrOptionsText, ...transcribeOptions } = options
  const formData = new FormData()
  formData.append('file', file)

  if (transcribeOptions.with_speaker !== undefined) {
    formData.append('with_speaker', String(transcribeOptions.with_speaker))
  }
  if (transcribeOptions.apply_hotword !== undefined) {
    formData.append('apply_hotword', String(transcribeOptions.apply_hotword))
  }
  if (transcribeOptions.apply_llm !== undefined) {
    formData.append('apply_llm', String(transcribeOptions.apply_llm))
  }
  if (transcribeOptions.llm_role) {
    formData.append('llm_role', transcribeOptions.llm_role)
  }
  if (transcribeOptions.hotwords) {
    formData.append('hotwords', transcribeOptions.hotwords)
  }

  // Per-request ASR tuning (backend validates allowlisted options).
  let asrOptions: Record<string, unknown> = parseAsrOptionsText(asrOptionsText)
  if (transcribeOptions.with_speaker) {
    asrOptions = mergeSpeakerLabelStyle(asrOptions, transcribeOptions.speaker_label_style || 'numeric')
  }
  if (Object.keys(asrOptions).length > 0) {
    formData.append('asr_options', JSON.stringify(asrOptions))
  }

  const response = await apiClient.post<TranscribeResponse>(
    '/api/v1/transcribe',
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      signal,
      onUploadProgress: onUploadProgress
        ? (progressEvent) => {
            const total = progressEvent.total || file.size
            const progress = Math.round((progressEvent.loaded * 100) / total)
            onUploadProgress(progress)
          }
        : undefined,
    }
  )
  return response.data
}

/**
 * 全量转写：跑全部模型并做 LLM 融合润色
 *
 * 注意：该接口会触发多个后端并发推理 + 1 次 LLM 调用，耗时会明显更长。
 */
export async function transcribeAllModels(
  file: File,
  options: TranscribeWithProgressOptions = {}
): Promise<EnsembleTranscribeResponse> {
  const { onUploadProgress, signal, asrOptionsText, ...transcribeOptions } = options
  const formData = new FormData()
  formData.append('file', file)

  // These options are still accepted by the backend:
  // - with_speaker: defaults to true on the backend for /transcribe/all
  // - apply_hotword: defaults to true
  // - apply_llm: defaults to true (LLM fusion)
  if (transcribeOptions.with_speaker !== undefined) {
    formData.append('with_speaker', String(transcribeOptions.with_speaker))
  }
  if (transcribeOptions.apply_hotword !== undefined) {
    formData.append('apply_hotword', String(transcribeOptions.apply_hotword))
  }
  if (transcribeOptions.apply_llm !== undefined) {
    formData.append('apply_llm', String(transcribeOptions.apply_llm))
  }
  if (transcribeOptions.llm_role) {
    formData.append('llm_role', transcribeOptions.llm_role)
  }
  if (transcribeOptions.hotwords) {
    formData.append('hotwords', transcribeOptions.hotwords)
  }

  // Per-request ASR tuning (backend validates allowlisted options).
  let asrOptions: Record<string, unknown> = parseAsrOptionsText(asrOptionsText)
  if (transcribeOptions.with_speaker) {
    asrOptions = mergeSpeakerLabelStyle(asrOptions, transcribeOptions.speaker_label_style || 'numeric')
  }
  if (Object.keys(asrOptions).length > 0) {
    formData.append('asr_options', JSON.stringify(asrOptions))
  }

  const response = await apiClient.post<EnsembleTranscribeResponse>(
    '/api/v1/transcribe/all',
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      signal,
      onUploadProgress: onUploadProgress
        ? (progressEvent) => {
            const total = progressEvent.total || file.size
            const progress = Math.round((progressEvent.loaded * 100) / total)
            onUploadProgress(progress)
          }
        : undefined,
    }
  )

  return response.data
}

/**
 * 批量文件转写 (带上传进度)
 */
export async function transcribeBatch(
  files: File[],
  options: TranscribeWithProgressOptions & { max_concurrent?: number } = {}
): Promise<BatchTranscribeResponse> {
  const { onUploadProgress, signal, asrOptionsText, ...transcribeOptions } = options
  const formData = new FormData()

  files.forEach((file) => {
    formData.append('files', file)
  })

  if (transcribeOptions.with_speaker !== undefined) {
    formData.append('with_speaker', String(transcribeOptions.with_speaker))
  }
  if (transcribeOptions.apply_hotword !== undefined) {
    formData.append('apply_hotword', String(transcribeOptions.apply_hotword))
  }
  if (transcribeOptions.apply_llm !== undefined) {
    formData.append('apply_llm', String(transcribeOptions.apply_llm))
  }
  if (transcribeOptions.llm_role) {
    formData.append('llm_role', transcribeOptions.llm_role)
  }
  if (transcribeOptions.hotwords) {
    formData.append('hotwords', transcribeOptions.hotwords)
  }
  if (transcribeOptions.max_concurrent !== undefined) {
    formData.append('max_concurrent', String(transcribeOptions.max_concurrent))
  }

  let asrOptions: Record<string, unknown> = parseAsrOptionsText(asrOptionsText)
  if (transcribeOptions.with_speaker) {
    asrOptions = mergeSpeakerLabelStyle(asrOptions, transcribeOptions.speaker_label_style || 'numeric')
  }
  if (Object.keys(asrOptions).length > 0) {
    formData.append('asr_options', JSON.stringify(asrOptions))
  }

  const totalSize = files.reduce((acc, f) => acc + f.size, 0)

  const response = await apiClient.post<BatchTranscribeResponse>(
    '/api/v1/transcribe/batch',
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      signal,
      onUploadProgress: onUploadProgress
        ? (progressEvent) => {
            const total = progressEvent.total || totalSize
            const progress = Math.round((progressEvent.loaded * 100) / total)
            onUploadProgress(progress)
          }
        : undefined,
    }
  )
  return response.data
}

/**
 * URL 转写 (异步)
 */
export async function transcribeUrl(
  url: string,
  options: UrlTranscribeOptions = {},
  overrides: ApiRequestOverrides = {}
): Promise<UrlTranscribeResponse> {
  const { signal, asrOptionsText, ...transcribeOptions } = options
  const formData = new FormData()
  formData.append('audio_url', url)

  if (transcribeOptions.with_speaker !== undefined) {
    formData.append('with_speaker', String(transcribeOptions.with_speaker))
  }
  if (transcribeOptions.apply_hotword !== undefined) {
    formData.append('apply_hotword', String(transcribeOptions.apply_hotword))
  }
  if (transcribeOptions.apply_llm !== undefined) {
    formData.append('apply_llm', String(transcribeOptions.apply_llm))
  }
  if (transcribeOptions.llm_role) {
    formData.append('llm_role', transcribeOptions.llm_role)
  }
  if (transcribeOptions.hotwords) {
    formData.append('hotwords', transcribeOptions.hotwords)
  }

  // Per-request ASR tuning (backend validates allowlisted options).
  let asrOptions: Record<string, unknown> = parseAsrOptionsText(asrOptionsText)
  if (transcribeOptions.with_speaker) {
    asrOptions = mergeSpeakerLabelStyle(asrOptions, transcribeOptions.speaker_label_style || 'numeric')
  }
  if (Object.keys(asrOptions).length > 0) {
    formData.append('asr_options', JSON.stringify(asrOptions))
  }

  const response = await apiClient.post<UrlTranscribeResponse>('/api/v1/trans/url', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    signal,
    baseURL: overrides.baseURL,
  })
  return response.data
}

/**
 * 查询异步任务结果
 */
export async function getTaskResult(
  taskId: string,
  options: { delete?: boolean; signal?: AbortSignal } = {},
  overrides: ApiRequestOverrides = {}
): Promise<TaskResultResponse> {
  const formData = new FormData()
  formData.append('task_id', taskId)
  formData.append('delete', String(options.delete ?? false))

  const response = await apiClient.post<TaskResultResponse>('/api/v1/result', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    signal: options.signal,
    baseURL: overrides.baseURL,
  })
  return response.data
}

/**
 * Whisper ASR WebService 兼容接口
 */
export async function transcribeWhisperCompatible(
  file: File,
  options: {
    fileType?: 'audio' | 'video'
    withSpeaker?: boolean
    applyHotword?: boolean
    signal?: AbortSignal
  } = {}
): Promise<WhisperAsrResponse> {
  const formData = new FormData()
  formData.append('file', file)

  if (options.fileType) {
    formData.append('file_type', options.fileType)
  }
  if (options.withSpeaker !== undefined) {
    formData.append('with_speaker', String(options.withSpeaker))
  }
  if (options.applyHotword !== undefined) {
    formData.append('apply_hotword', String(options.applyHotword))
  }

  const response = await apiClient.post<WhisperAsrResponse>('/api/v1/asr', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    signal: options.signal,
  })
  return response.data
}

/**
 * 视频转写 (带上传进度)
 */
export async function transcribeVideo(
  file: File,
  options: TranscribeWithProgressOptions = {}
): Promise<VideoTranscribeResponse> {
  const { onUploadProgress, signal, asrOptionsText, ...transcribeOptions } = options
  const formData = new FormData()
  formData.append('file', file)

  if (transcribeOptions.with_speaker !== undefined) {
    formData.append('with_speaker', String(transcribeOptions.with_speaker))
  }
  if (transcribeOptions.apply_hotword !== undefined) {
    formData.append('apply_hotword', String(transcribeOptions.apply_hotword))
  }
  if (transcribeOptions.apply_llm !== undefined) {
    formData.append('apply_llm', String(transcribeOptions.apply_llm))
  }
  if (transcribeOptions.llm_role) {
    formData.append('llm_role', transcribeOptions.llm_role)
  }
  if (transcribeOptions.hotwords) {
    formData.append('hotwords', transcribeOptions.hotwords)
  }

  let asrOptions: Record<string, unknown> = parseAsrOptionsText(asrOptionsText)
  if (transcribeOptions.with_speaker) {
    asrOptions = mergeSpeakerLabelStyle(asrOptions, transcribeOptions.speaker_label_style || 'numeric')
  }
  if (Object.keys(asrOptions).length > 0) {
    formData.append('asr_options', JSON.stringify(asrOptions))
  }

  const response = await apiClient.post<VideoTranscribeResponse>(
    '/api/v1/trans/video',
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      signal,
      onUploadProgress: onUploadProgress
        ? (progressEvent) => {
            const total = progressEvent.total || file.size
            const progress = Math.round((progressEvent.loaded * 100) / total)
            onUploadProgress(progress)
          }
        : undefined,
    }
  )
  return response.data
}

/**
 * 判断文件是否为视频
 */
export function isVideoFile(file: File): boolean {
  const videoTypes = ['video/mp4', 'video/avi', 'video/x-msvideo', 'video/quicktime', 'video/x-matroska', 'video/webm']
  const videoExtensions = ['.mp4', '.avi', '.mov', '.mkv', '.webm']

  if (videoTypes.includes(file.type)) return true

  const ext = file.name.toLowerCase().slice(file.name.lastIndexOf('.'))
  return videoExtensions.includes(ext)
}

/**
 * 智能转写 - 根据文件类型选择接口
 */
export async function transcribeSmart(
  file: File,
  options: TranscribeWithProgressOptions = {}
): Promise<TranscribeResponse> {
  if (isVideoFile(file)) {
    return transcribeVideo(file, options)
  }
  return transcribeAudio(file, options)
}
