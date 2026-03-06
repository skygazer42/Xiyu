import apiClient from './client'
import type { UploadProgressCallback } from './transcribe'

export interface EnhanceAudioOptions {
  onUploadProgress?: UploadProgressCallback
  signal?: AbortSignal
  /**
   * Advanced per-request ASR tuning options (`asr_options`) as a JSON string.
   *
   * Only `asr_options.preprocess` is used by the backend endpoint.
   */
  asrOptionsText?: string
}

/**
 * 音频增强（预处理）：
 * - 复用后端 `process_audio_file()` 的预处理链路（如 ClearVoice 降噪）
 * - 返回增强后的 WAV（16kHz mono PCM16）
 */
export async function enhanceAudio(file: File, options: EnhanceAudioOptions = {}): Promise<Blob> {
  const { onUploadProgress, signal, asrOptionsText } = options
  const formData = new FormData()
  formData.append('file', file)

  const asr = (asrOptionsText || '').trim()
  if (asr) {
    formData.append('asr_options', asr)
  }

  const response = await apiClient.post<Blob>('/api/v1/preprocess/enhance', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    responseType: 'blob',
    // ClearVoice enhancement can be significantly slower than plain ASR,
    // especially on CPU; keep generous timeout.
    timeout: 10 * 60 * 1000,
    signal,
    onUploadProgress: onUploadProgress
      ? (progressEvent) => {
          const total = progressEvent.total || file.size
          const progress = Math.round((progressEvent.loaded * 100) / total)
          onUploadProgress(progress)
        }
      : undefined,
  })

  return response.data
}

