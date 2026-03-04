import { create } from 'zustand'
import type { TranscribeResponse, TranscribeOptions, SentenceInfo, EnsembleLlmRole } from '@/lib/api/types'

interface EnsembleOptions {
  apply_llm: boolean
  llm_role: EnsembleLlmRole
  include_srt: boolean
}

interface TranscriptionState {
  // 当前转写结果
  result: TranscribeResponse | null
  setResult: (result: TranscribeResponse | null) => void

  // 转写选项
  options: TranscribeOptions
  setOptions: (options: Partial<TranscribeOptions>) => void

  // 全量优化（多模型融合）选项
  ensembleOptions: EnsembleOptions
  setEnsembleOptions: (options: Partial<EnsembleOptions>) => void

  // 高级 asr_options（JSON 文本，直接透传到后端的 allowlist 解析）
  advancedAsrOptionsText: string
  setAdvancedAsrOptionsText: (text: string) => void
  advancedAsrOptionsError: string | null
  setAdvancedAsrOptionsError: (error: string | null) => void

  // 临时热词
  tempHotwords: string
  setTempHotwords: (hotwords: string) => void

  // 上传的文件
  files: File[]
  setFiles: (files: File[]) => void
  addFiles: (files: File[]) => void
  removeFile: (index: number) => void
  clearFiles: () => void

  // 转写状态
  isTranscribing: boolean
  setTranscribing: (isTranscribing: boolean) => void

  // 选中的句子
  selectedSentence: SentenceInfo | null
  setSelectedSentence: (sentence: SentenceInfo | null) => void

  // 清空所有
  reset: () => void
}

const defaultOptions: TranscribeOptions = {
  with_speaker: true,
  apply_hotword: true,
  apply_llm: false,
  llm_role: 'policy_polish_balanced',
  speaker_label_style: 'zh',
  target_backend: 'auto',
}

const defaultEnsembleOptions: EnsembleOptions = {
  apply_llm: true,
  llm_role: 'policy_meeting_aggressive',
  include_srt: true,
}

export const useTranscriptionStore = create<TranscriptionState>((set) => ({
  // 当前转写结果
  result: null,
  setResult: (result) => set({ result }),

  // 转写选项
  options: defaultOptions,
  setOptions: (options) =>
    set((state) => ({ options: { ...state.options, ...options } })),

  // 全量优化选项
  ensembleOptions: defaultEnsembleOptions,
  setEnsembleOptions: (options) =>
    set((state) => ({ ensembleOptions: { ...state.ensembleOptions, ...options } })),

  // 高级 asr_options
  advancedAsrOptionsText: '',
  setAdvancedAsrOptionsText: (advancedAsrOptionsText) => set({ advancedAsrOptionsText }),
  advancedAsrOptionsError: null,
  setAdvancedAsrOptionsError: (advancedAsrOptionsError) => set({ advancedAsrOptionsError }),

  // 临时热词
  tempHotwords: '',
  setTempHotwords: (tempHotwords) => set({ tempHotwords }),

  // 上传的文件
  files: [],
  setFiles: (files) => set({ files }),
  addFiles: (newFiles) => set((state) => ({ files: [...state.files, ...newFiles] })),
  removeFile: (index) =>
    set((state) => ({
      files: state.files.filter((_, i) => i !== index),
    })),
  clearFiles: () => set({ files: [] }),

  // 转写状态
  isTranscribing: false,
  setTranscribing: (isTranscribing) => set({ isTranscribing }),

  // 选中的句子
  selectedSentence: null,
  setSelectedSentence: (selectedSentence) => set({ selectedSentence }),

  // 清空所有
  reset: () =>
    set({
      result: null,
      files: [],
      isTranscribing: false,
      selectedSentence: null,
      tempHotwords: '',
      advancedAsrOptionsText: '',
      advancedAsrOptionsError: null,
      options: defaultOptions,
      ensembleOptions: defaultEnsembleOptions,
    }),
}))
