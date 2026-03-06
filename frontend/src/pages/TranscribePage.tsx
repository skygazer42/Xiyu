import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CircularProgressIndeterminate } from '@/components/ui/circular-progress'
import { Loader2, Play, FileAudio, Link, Download } from 'lucide-react'
import { FileDropzone } from '@/components/upload'
import { TranscribeOptions } from '@/components/transcribe'
import { EnsemblePanel, TranscriptView } from '@/components/transcript'
import { Timeline } from '@/components/timeline'
import { HistoryList } from '@/components/history/HistoryList'
import { UrlTranscribe } from '@/components/url/UrlTranscribe'
import { TaskManager, type Task } from '@/components/task/TaskManager'
import { useTranscriptionStore } from '@/stores'
import { useHistoryStore, type HistoryItem } from '@/stores/historyStore'
import {
  getApiBaseUrl,
  getTaskResult,
  transcribeAudio,
  transcribeAllModels,
  transcribeBatch,
  enhanceAudio,
  transcribeFileAsync,
  transcribeUrl,
} from '@/lib/api'
import { fromAxiosError, getUserFriendlyMessage } from '@/lib/errors'
import type { EnsembleTranscribeResponse, SentenceInfo, TranscribeResponse } from '@/lib/api/types'

type AsyncTranscribeTask = Task & {
  backendBaseUrl: string
  kind: 'url' | 'file'
  savedToHistory?: boolean
}

const URL_TASK_MAX_POLL_MS = 10 * 60 * 1000
const FILE_TASK_MAX_POLL_MS = 24 * 60 * 60 * 1000
const TASKS_STORAGE_KEY = 'xiyu_async_tasks_v1'

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function buildMergedAsrOptionsText(
  advancedText: string,
  preprocess: { clearvoice_denoise_enable: boolean }
): string | undefined {
  const raw = (advancedText || '').trim()
  let obj: Record<string, unknown> = {}

  if (raw) {
    try {
      const parsed: unknown = JSON.parse(raw)
      if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
        obj = parsed as Record<string, unknown>
      } else {
        // Keep invalid types as-is; backend/UI will reject on submit anyway.
        return raw
      }
    } catch {
      // Keep invalid JSON as-is; submit is blocked by advancedAsrOptionsError.
      return raw
    }
  }

  let changed = false

  if (preprocess.clearvoice_denoise_enable) {
    const existing = obj.preprocess
    const preprocessSection: Record<string, unknown> =
      existing && typeof existing === 'object' && !Array.isArray(existing)
        ? { ...(existing as Record<string, unknown>) }
        : {}
    preprocessSection.denoise_enable = true
    preprocessSection.denoise_backend = 'clearvoice'
    obj = { ...obj, preprocess: preprocessSection }
    changed = true
  }

  if (!raw && !changed) {
    return undefined
  }

  if (!changed) {
    return raw
  }

  return JSON.stringify(obj)
}

export default function TranscribePage() {
  const {
    files,
    options,
    ensembleOptions,
    setEnsembleOptions,
    tempHotwords,
    advancedAsrOptionsText,
    advancedAsrOptionsError,
    preprocess,
    result,
    setResult,
    isTranscribing,
    setTranscribing,
    clearFiles,
    setSelectedSentence,
  } = useTranscriptionStore()

  const { addItem } = useHistoryStore()

  const [selectedIndex, setSelectedIndex] = useState<number>()
  const [inputMode, setInputMode] = useState<string>('file')
  const [asyncTasks, setAsyncTasks] = useState<AsyncTranscribeTask[]>([])
  const [isSubmittingUrl, setIsSubmittingUrl] = useState(false)
  const [isSubmittingFile, setIsSubmittingFile] = useState(false)
  const [fileSubmitProgress, setFileSubmitProgress] = useState<number | null>(null)
  const [fileSubmitName, setFileSubmitName] = useState<string | null>(null)
  const [resultFilename, setResultFilename] = useState<string | undefined>()
  const [ensembleResult, setEnsembleResult] = useState<EnsembleTranscribeResponse | null>(null)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [transcribePhase, setTranscribePhase] = useState<'idle' | 'uploading' | 'processing'>('idle')
  const [elapsedMs, setElapsedMs] = useState(0)
  const [isEnhancing, setIsEnhancing] = useState(false)

  const ensembleQuickMode = useMemo(() => {
    if (!ensembleOptions.apply_llm) {
      return 'compare'
    }
    return ensembleOptions.llm_role
  }, [ensembleOptions.apply_llm, ensembleOptions.llm_role])

  const ensembleButtonLabel = useMemo(() => {
    return ensembleOptions.apply_llm ? '全量优化' : '全量对比'
  }, [ensembleOptions.apply_llm])

  const mergedAsrOptionsText = useMemo(() => {
    return buildMergedAsrOptionsText(advancedAsrOptionsText, preprocess)
  }, [advancedAsrOptionsText, preprocess])

  const taskPollingTimersRef = useRef<Record<string, number>>({})
  const taskPollingStartedAtRef = useRef<Record<string, number>>({})
  const abortControllerRef = useRef<AbortController | null>(null)
  const transcribeStartedAtRef = useRef<number | null>(null)

  useEffect(() => {
    if (!isTranscribing) {
      setElapsedMs(0)
      return
    }

    const timerId = window.setInterval(() => {
      const startedAt = transcribeStartedAtRef.current
      if (!startedAt) return
      setElapsedMs(Date.now() - startedAt)
    }, 500)

    return () => window.clearInterval(timerId)
  }, [isTranscribing])

  const resetTranscribeUiState = useCallback(() => {
    setUploadProgress(null)
    setTranscribePhase('idle')
    setElapsedMs(0)
    transcribeStartedAtRef.current = null
    abortControllerRef.current = null
  }, [])

  const handleCancel = useCallback(() => {
    const controller = abortControllerRef.current
    if (!controller) return
    controller.abort()
    resetTranscribeUiState()
    setTranscribing(false)
  }, [resetTranscribeUiState, setTranscribing])

  const stopTaskPolling = useCallback((taskId: string) => {
    const timerId = taskPollingTimersRef.current[taskId]
    if (timerId !== undefined) {
      window.clearTimeout(timerId)
      delete taskPollingTimersRef.current[taskId]
    }
    delete taskPollingStartedAtRef.current[taskId]
  }, [])

  useEffect(() => {
    return () => {
      for (const timerId of Object.values(taskPollingTimersRef.current)) {
        window.clearTimeout(timerId)
      }
      taskPollingTimersRef.current = {}
      taskPollingStartedAtRef.current = {}
    }
  }, [])

  const updateAsyncTask = useCallback(
    (taskId: string, updater: (task: AsyncTranscribeTask) => AsyncTranscribeTask) => {
      setAsyncTasks((prev) => prev.map((t) => (t.id === taskId ? updater(t) : t)))
    },
    []
  )

  const extractFilenameFromUrl = useCallback((rawUrl: string): string | undefined => {
    try {
      const u = new URL(rawUrl)
      const parts = u.pathname.split('/').filter(Boolean)
      const last = parts[parts.length - 1]
      if (!last) {
        return undefined
      }
      return decodeURIComponent(last)
    } catch {
      return undefined
    }
  }, [])

  const isTranscribeResponse = useCallback((value: unknown): value is TranscribeResponse => {
    if (!value || typeof value !== 'object') return false
    const obj = value as { sentences?: unknown; text?: unknown; code?: unknown }
    return Array.isArray(obj.sentences) && typeof obj.text === 'string' && typeof obj.code === 'number'
  }, [])

  const startTaskPolling = useCallback(
    (taskId: string, backendBaseUrl: string, kind: AsyncTranscribeTask['kind']) => {
      if (taskPollingTimersRef.current[taskId] !== undefined) {
        return
      }

      taskPollingStartedAtRef.current[taskId] = Date.now()
      const maxPollMs = kind === 'file' ? FILE_TASK_MAX_POLL_MS : URL_TASK_MAX_POLL_MS

      const pollOnce = async () => {
        const startedAt = taskPollingStartedAtRef.current[taskId] ?? Date.now()
        if (Date.now() - startedAt > maxPollMs) {
          stopTaskPolling(taskId)
          updateAsyncTask(taskId, (t) => ({
            ...t,
            status: 'error',
            error: '任务轮询超时，请检查服务或稍后重试',
          }))
          return
        }

        try {
          const response = await getTaskResult(taskId, { delete: false }, { baseURL: backendBaseUrl })

          if (response.status === 'pending' || response.status === 'processing') {
            const meta = response.data
            const progress =
              meta && typeof meta === 'object' && !Array.isArray(meta) && 'progress' in meta
                ? (meta as { progress?: unknown }).progress
                : undefined
            const detail =
              meta && typeof meta === 'object' && !Array.isArray(meta) && 'detail' in meta
                ? (meta as { detail?: unknown }).detail
                : undefined

            updateAsyncTask(taskId, (t) => ({
              ...t,
              status: response.status,
              progress: typeof progress === 'number' ? progress : t.progress,
              detail: typeof detail === 'string' ? detail : t.detail,
            }))

            const delayMs = response.status === 'processing' ? 2000 : 1000
            taskPollingTimersRef.current[taskId] = window.setTimeout(pollOnce, delayMs)
            return
          }

          if (response.status === 'success') {
            stopTaskPolling(taskId)
            const data = response.data
            if (!isTranscribeResponse(data)) {
              updateAsyncTask(taskId, (t) => ({
                ...t,
                status: 'error',
                error: '任务返回格式异常（缺少转写结果）',
              }))
              return
            }

            updateAsyncTask(taskId, (t) => ({
              ...t,
              status: 'success',
              result: data,
              progress: 100,
              detail: undefined,
            }))

            toast.success(kind === 'file' ? '文件任务转写完成' : 'URL 转写完成')
            return
          }

          // error
          stopTaskPolling(taskId)
          updateAsyncTask(taskId, (t) => ({
            ...t,
            status: 'error',
            error: response.message || '任务失败',
          }))
        } catch (error) {
          stopTaskPolling(taskId)
          updateAsyncTask(taskId, (t) => ({
            ...t,
            status: 'error',
            error: error instanceof Error ? error.message : '请求失败',
          }))
        }
      }

      void pollOnce()
    },
    [isTranscribeResponse, stopTaskPolling, updateAsyncTask]
  )

  useEffect(() => {
    try {
      const raw = localStorage.getItem(TASKS_STORAGE_KEY)
      if (!raw) return
      const parsed: unknown = JSON.parse(raw)
      if (!Array.isArray(parsed)) return

      const restored: AsyncTranscribeTask[] = parsed
        .map((t): AsyncTranscribeTask | null => {
          if (!t || typeof t !== 'object') return null
          const obj = t as Record<string, unknown>
          const id = typeof obj.id === 'string' ? obj.id : null
          const kind = obj.kind === 'file' || obj.kind === 'url' ? (obj.kind as 'file' | 'url') : null
          if (!id || !kind) return null

          const createdAt =
            typeof obj.createdAt === 'string' || obj.createdAt instanceof Date
              ? new Date(obj.createdAt as string)
              : new Date()

          const task: AsyncTranscribeTask = {
            id,
            kind,
            status:
              obj.status === 'pending' || obj.status === 'processing' || obj.status === 'success' || obj.status === 'error'
                ? (obj.status as AsyncTranscribeTask['status'])
                : 'pending',
            createdAt,
            backendBaseUrl: typeof obj.backendBaseUrl === 'string' ? obj.backendBaseUrl : getApiBaseUrl(),
          }

          if (typeof obj.url === 'string') task.url = obj.url
          if (typeof obj.filename === 'string') task.filename = obj.filename
          if (typeof obj.error === 'string') task.error = obj.error
          if (typeof obj.progress === 'number') task.progress = obj.progress
          if (typeof obj.detail === 'string') task.detail = obj.detail
          return task
        })
        .filter((t): t is AsyncTranscribeTask => t !== null)

      if (restored.length > 0) {
        setAsyncTasks(restored)
        restored.forEach((t) => {
          if (t.status === 'pending' || t.status === 'processing') {
            startTaskPolling(t.id, t.backendBaseUrl, t.kind)
          }
        })
      }
    } catch {
      // ignore corrupted localStorage
    }
  }, [startTaskPolling])

  useEffect(() => {
    try {
      const minimal = asyncTasks.map((t) => ({
        id: t.id,
        kind: t.kind,
        status: t.status,
        url: t.url,
        filename: t.filename,
        createdAt: t.createdAt.toISOString(),
        backendBaseUrl: t.backendBaseUrl,
        error: t.error,
        progress: t.progress,
        detail: t.detail,
      }))
      localStorage.setItem(TASKS_STORAGE_KEY, JSON.stringify(minimal))
    } catch {
      // ignore quota errors
    }
  }, [asyncTasks])

  const handleDownloadEnhancedAudio = async () => {
    if (files.length === 0) {
      toast.error('请先上传音频文件')
      return
    }
    if (files.length !== 1) {
      toast.error('降噪下载目前仅支持单文件')
      return
    }
    if (!preprocess.clearvoice_denoise_enable) {
      toast.error('请先开启 ClearVoice 降噪')
      return
    }
    if (advancedAsrOptionsError) {
      toast.error(`高级 asr_options JSON 无效：${advancedAsrOptionsError}`)
      return
    }

    setIsEnhancing(true)
    try {
      const blob = await enhanceAudio(files[0], {
        asrOptionsText: mergedAsrOptionsText,
      })
      const stem = files[0].name.replace(/\.[^/.]+$/, '') || 'audio'
      downloadBlob(`${stem}.denoised.wav`, blob)
      toast.success('已下载降噪音频')
    } catch (error) {
      console.error('Enhance audio error:', error)
      const appError = fromAxiosError(error)
      toast.error(getUserFriendlyMessage(appError))
    } finally {
      setIsEnhancing(false)
    }
  }

  const handleTranscribe = async () => {
    if (files.length === 0) {
      toast.error('请先上传音频文件')
      return
    }

    if (advancedAsrOptionsError) {
      toast.error(`高级 asr_options JSON 无效：${advancedAsrOptionsError}`)
      return
    }

    setTranscribing(true)
    transcribeStartedAtRef.current = Date.now()
    abortControllerRef.current = new AbortController()
    setUploadProgress(0)
    setTranscribePhase('uploading')
    setEnsembleResult(null)
    setResult(null)
    setResultFilename(undefined)

    try {
        const transcribeOptions = {
          ...options,
          hotwords: tempHotwords || undefined,
          asrOptionsText: mergedAsrOptionsText,
        }

      if (files.length === 1) {
        // 单文件转写
        const response = await transcribeAudio(files[0], {
          ...transcribeOptions,
          signal: abortControllerRef.current?.signal,
          onUploadProgress: (progress) => {
            const p = Math.max(0, Math.min(100, progress))
            if (p >= 99) {
              setUploadProgress(null)
              setTranscribePhase('processing')
              return
            }
            setUploadProgress(p)
          },
        })
        if (response.code === 0) {
          setResult(response)
          setResultFilename(files[0].name.replace(/\.[^/.]+$/, ''))
          // 保存到历史记录
          addItem({
            filename: files[0].name,
            text: response.text,
            textAccu: response.text_accu ?? undefined,
            sentences: response.sentences,
            speakerTurns: response.speaker_turns ?? undefined,
            transcript: response.transcript ?? undefined,
            srt: response.srt ?? undefined,
            rawText: response.raw_text,
            options: {
              withSpeaker: options.with_speaker,
              applyHotword: options.apply_hotword,
              applyLlm: options.apply_llm,
              llmRole: options.llm_role,
            },
          })
          toast.success('转写完成')
        } else {
          toast.error('转写失败')
        }
      } else {
        // 批量转写
        const response = await transcribeBatch(files, {
          ...transcribeOptions,
          signal: abortControllerRef.current?.signal,
          onUploadProgress: (progress) => {
            const p = Math.max(0, Math.min(100, progress))
            if (p >= 99) {
              setUploadProgress(null)
              setTranscribePhase('processing')
              return
            }
            setUploadProgress(p)
          },
        })
        if (response.success_count > 0) {
          // 显示第一个成功的结果
          const firstSuccess = response.results.find(r => r.success && r.result)
          if (firstSuccess?.result) {
            setResult(firstSuccess.result)
            setResultFilename((firstSuccess.filename || '').replace(/\.[^/.]+$/, '') || undefined)
          }
          // 保存批量结果到历史
          response.results.forEach((r, idx) => {
            if (r.success && r.result) {
              addItem({
                filename: files[idx]?.name ?? `文件${idx + 1}`,
                text: r.result.text,
                textAccu: r.result.text_accu ?? undefined,
                sentences: r.result.sentences,
                speakerTurns: r.result.speaker_turns ?? undefined,
                transcript: r.result.transcript ?? undefined,
                srt: r.result.srt ?? undefined,
                rawText: r.result.raw_text,
                options: {
                  withSpeaker: options.with_speaker,
                  applyHotword: options.apply_hotword,
                  applyLlm: options.apply_llm,
                  llmRole: options.llm_role,
                },
              })
            }
          })
          toast.success(`转写完成: ${response.success_count}/${response.total} 成功`)
        } else {
          toast.error('所有文件转写失败')
        }
      }
    } catch (error) {
      console.error('Transcription error:', error)
      const isCanceled =
        typeof error === 'object' &&
        error !== null &&
        'code' in error &&
        (error as { code?: string }).code === 'ERR_CANCELED'
      if (isCanceled) {
        toast.message('已取消')
        return
      }
      if (error instanceof Error && error.message.includes('asr_options')) {
        toast.error(error.message)
      } else {
        const appError = fromAxiosError(error)
        toast.error(getUserFriendlyMessage(appError))
      }
    } finally {
      setTranscribing(false)
      resetTranscribeUiState()
    }
  }

  const handleTranscribeAllModels = async () => {
    if (files.length === 0) {
      toast.error('请先上传音频文件')
      return
    }
    if (files.length !== 1) {
      toast.error('全量模式目前仅支持单文件')
      return
    }

    if (advancedAsrOptionsError) {
      toast.error(`高级 asr_options JSON 无效：${advancedAsrOptionsError}`)
      return
    }

    setTranscribing(true)
    transcribeStartedAtRef.current = Date.now()
    abortControllerRef.current = new AbortController()
    setUploadProgress(0)
    setTranscribePhase('uploading')
    setEnsembleResult(null)
    setResult(null)
    setResultFilename(undefined)

    try {
      const transcribeOptions = {
        with_speaker: options.with_speaker,
        apply_hotword: options.apply_hotword,
        apply_llm: ensembleOptions.apply_llm,
        llm_role: ensembleOptions.llm_role,
        include_srt: ensembleOptions.include_srt,
        hotwords: tempHotwords || undefined,
        asrOptionsText: mergedAsrOptionsText,
        speaker_label_style: options.speaker_label_style,
      }

      const response = await transcribeAllModels(files[0], {
        ...transcribeOptions,
        signal: abortControllerRef.current?.signal,
        onUploadProgress: (progress) => {
          const p = Math.max(0, Math.min(100, progress))
          if (p >= 99) {
            setUploadProgress(null)
            setTranscribePhase('processing')
            return
          }
          setUploadProgress(p)
        },
      })
      if (response.code === 0) {
        setEnsembleResult(response)
        setResult(response.final)
        setResultFilename(files[0].name.replace(/\.[^/.]+$/, ''))
        addItem({
          filename: files[0].name,
          text: response.final.text,
          textAccu: response.final.text_accu ?? undefined,
          sentences: response.final.sentences,
          speakerTurns: response.final.speaker_turns ?? undefined,
          transcript: response.final.transcript ?? undefined,
          srt: response.final.srt ?? undefined,
          rawText: response.final.raw_text,
          options: {
            withSpeaker: options.with_speaker,
            applyHotword: options.apply_hotword,
            applyLlm: ensembleOptions.apply_llm,
            llmRole: ensembleOptions.llm_role,
          },
        })
        if (ensembleOptions.apply_llm && response.llm_used === false) {
          toast.message('全量完成：多模型已跑完，但 LLM 未实际调用（检查 LLM_ENABLE/网络/API Key）')
        } else {
          toast.success(ensembleOptions.apply_llm ? '全量融合完成' : '全量对比完成')
        }
      } else {
        toast.error(ensembleOptions.apply_llm ? '全量融合失败' : '全量对比失败')
      }
    } catch (error) {
      console.error('Ensemble transcription error:', error)
      const isCanceled =
        typeof error === 'object' &&
        error !== null &&
        'code' in error &&
        (error as { code?: string }).code === 'ERR_CANCELED'
      if (isCanceled) {
        toast.message('已取消')
        return
      }
      const appError = fromAxiosError(error)
      toast.error(getUserFriendlyMessage(appError))
    } finally {
      setTranscribing(false)
      resetTranscribeUiState()
    }
  }

  const urlTranscribeOptions = useMemo(() => {
    return {
      ...options,
      hotwords: tempHotwords || undefined,
      asrOptionsText: mergedAsrOptionsText,
    }
  }, [mergedAsrOptionsText, options, tempHotwords])

  const urlTasks = useMemo(() => asyncTasks.filter((t) => t.kind === 'url'), [asyncTasks])
  const fileTasks = useMemo(() => asyncTasks.filter((t) => t.kind === 'file'), [asyncTasks])

  const handleUrlSubmit = async (url: string) => {
    if (advancedAsrOptionsError) {
      toast.error(`高级 asr_options JSON 无效：${advancedAsrOptionsError}`)
      return
    }

    setIsSubmittingUrl(true)

    const backendBaseUrl = getApiBaseUrl()
    const filename = extractFilenameFromUrl(url)

    try {
      const response = await transcribeUrl(url, urlTranscribeOptions, { baseURL: backendBaseUrl })
      const taskId = response.data?.task_id
      if (!taskId) {
        toast.error(response.message || 'URL 转写提交失败')
        return
      }

      const task: AsyncTranscribeTask = {
        id: taskId,
        kind: 'url',
        status: 'pending',
        url,
        filename: filename || taskId,
        createdAt: new Date(),
        backendBaseUrl,
      }

      setAsyncTasks((prev) => [task, ...prev])
      toast.success('URL 转写任务已提交')
      startTaskPolling(taskId, backendBaseUrl, 'url')
    } catch (error) {
      console.error('URL transcription submit error:', error)
      toast.error('URL 转写提交失败，请检查服务连接')
    } finally {
      setIsSubmittingUrl(false)
    }
  }

  const handleSubmitFileTasks = async () => {
    if (files.length === 0) {
      toast.error('请先上传音频文件')
      return
    }
    if (advancedAsrOptionsError) {
      toast.error(`高级 asr_options JSON 无效：${advancedAsrOptionsError}`)
      return
    }

    setIsSubmittingFile(true)
    setFileSubmitProgress(0)
    const backendBaseUrl = getApiBaseUrl()

    let submitted = 0
    try {
      for (const f of files) {
        setFileSubmitName(f.name)
        setFileSubmitProgress(0)

        try {
          const response = await transcribeFileAsync(
            f,
            {
              ...urlTranscribeOptions,
              onUploadProgress: (progress) => {
                const p = Math.max(0, Math.min(100, progress))
                setFileSubmitProgress(p)
              },
            },
            { baseURL: backendBaseUrl }
          )
          const taskId = response.data?.task_id
          if (!taskId) {
            toast.error(response.message || `${f.name} 提交失败`)
            continue
          }

          const task: AsyncTranscribeTask = {
            id: taskId,
            kind: 'file',
            status: 'pending',
            filename: f.name,
            createdAt: new Date(),
            backendBaseUrl,
          }

          setAsyncTasks((prev) => [task, ...prev])
          startTaskPolling(taskId, backendBaseUrl, 'file')
          submitted += 1
        } catch (error) {
          console.error('File task submit error:', error)
          toast.error(`${f.name} 提交失败`)
        }
      }

      if (submitted > 0) {
        toast.success(`已提交 ${submitted}/${files.length} 个文件任务`)
      } else {
        toast.error('文件任务提交失败')
      }
    } finally {
      setIsSubmittingFile(false)
      setFileSubmitProgress(null)
      setFileSubmitName(null)
    }
  }

  const handleViewAsyncTaskResult = async (task: AsyncTranscribeTask) => {
    const backendBaseUrl = task.backendBaseUrl || getApiBaseUrl()
    let data: TranscribeResponse | null = task.result ?? null

    if (!data) {
      try {
        const response = await getTaskResult(task.id, { delete: false }, { baseURL: backendBaseUrl })
        if (response.status !== 'success' || !isTranscribeResponse(response.data)) {
          toast.error(response.message || '任务暂无可查看的结果')
          return
        }
        data = response.data
        updateAsyncTask(task.id, (t) => ({ ...t, status: 'success', result: data! }))
      } catch (error) {
        console.error('Fetch task result error:', error)
        toast.error('拉取任务结果失败，请检查服务连接')
        return
      }
    }

    setEnsembleResult(null)
    setResult(data)
    setResultFilename((task.filename || task.id).replace(/\.[^/.]+$/, ''))
    setSelectedSentence(null)
    setSelectedIndex(undefined)

    if (!task.savedToHistory) {
      addItem({
        filename: task.filename || task.id,
        text: data.text,
        textAccu: data.text_accu ?? undefined,
        sentences: data.sentences,
        speakerTurns: data.speaker_turns ?? undefined,
        transcript: data.transcript ?? undefined,
        srt: data.srt ?? undefined,
        rawText: data.raw_text,
        options: {
          withSpeaker: options.with_speaker,
          applyHotword: options.apply_hotword,
          applyLlm: options.apply_llm,
          llmRole: options.llm_role,
        },
      })
      updateAsyncTask(task.id, (t) => ({ ...t, savedToHistory: true }))

      // Best-effort cleanup: once the user has loaded the result into history, free backend cache.
      void getTaskResult(task.id, { delete: true }, { baseURL: backendBaseUrl }).catch(() => {})
    }

    toast.success('已加载任务结果')
  }

  const handleRemoveAsyncTask = (taskId: string) => {
    stopTaskPolling(taskId)
    const task = asyncTasks.find((t) => t.id === taskId)
    setAsyncTasks((prev) => prev.filter((t) => t.id !== taskId))
    if (task) {
      void getTaskResult(taskId, { delete: true }, { baseURL: task.backendBaseUrl }).catch(() => {})
    }
  }

  const handleRefreshAsyncTask = (taskId: string) => {
    const task = asyncTasks.find((t) => t.id === taskId)
    if (!task) return

    stopTaskPolling(taskId)
    updateAsyncTask(taskId, (t) => ({ ...t, status: 'pending', error: undefined }))
    startTaskPolling(taskId, task.backendBaseUrl, task.kind)
  }

  const handleRetryUrlTask = (task: AsyncTranscribeTask) => {
    if (task.kind !== 'url' || !task.url) {
      toast.error('无法重试：缺少 URL')
      return
    }
    void handleUrlSubmit(task.url)
  }

  const handleSelectSentence = (sentence: SentenceInfo, index: number) => {
    setSelectedSentence(sentence)
    setSelectedIndex(index)
  }

  const handleClear = () => {
    clearFiles()
    setEnsembleResult(null)
    setResult(null)
    setResultFilename(undefined)
    setSelectedSentence(null)
    setSelectedIndex(undefined)
  }

  const handleViewHistoryItem = (item: HistoryItem) => {
    setEnsembleResult(null)
    setResult({
      code: 0,
      text: item.text,
      text_accu: item.textAccu ?? null,
      sentences: item.sentences,
      speaker_turns: item.speakerTurns ?? null,
      transcript: item.transcript,
      srt: item.srt ?? null,
      raw_text: item.rawText,
    })
    setResultFilename(item.filename.replace(/\.[^/.]+$/, ''))
    setSelectedSentence(null)
    setSelectedIndex(undefined)
    toast.success('已加载历史记录')
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">音频转写</h1>
        <p className="text-muted-foreground">上传音频文件进行语音识别和转写</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* 输入区域 - 文件上传 / URL 输入 */}
        <Card>
          <CardHeader className="pb-3">
            <Tabs value={inputMode} onValueChange={setInputMode}>
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="file">
                  <FileAudio className="h-4 w-4 mr-1" />
                  文件上传
                </TabsTrigger>
                <TabsTrigger value="url">
                  <Link className="h-4 w-4 mr-1" />
                  URL转写
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </CardHeader>
          <CardContent className="space-y-4">
            {inputMode === 'file' ? (
              <>
                <FileDropzone />
                <div className="flex gap-2">
                  <Button
                    className="flex-1"
                    onClick={handleTranscribe}
                    disabled={files.length === 0 || isTranscribing}
                  >
                    {isTranscribing ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        转写中...
                      </>
                    ) : (
                      <>
                        <Play className="h-4 w-4 mr-2" />
                        开始转写
                      </>
                    )}
                  </Button>
	                  <Button
	                    variant="secondary"
	                    onClick={handleTranscribeAllModels}
	                    disabled={files.length !== 1 || isTranscribing}
	                    title="同时跑全部模型（可选 LLM 融合润色，耗时更长）"
	                  >
	                    {ensembleButtonLabel}
	                  </Button>
	                  <Select
	                    value={ensembleQuickMode}
	                    onValueChange={(value) => {
	                      if (value === 'compare') {
	                        setEnsembleOptions({ apply_llm: false })
	                        return
	                      }
	                      setEnsembleOptions({ apply_llm: true, llm_role: value as typeof ensembleOptions.llm_role })
	                    }}
	                    disabled={isTranscribing}
	                  >
	                    <SelectTrigger size="sm" className="min-w-[9.5rem]" title="全量优化：LLM 融合润色程度">
	                      <SelectValue placeholder="融合程度" />
	                    </SelectTrigger>
	                    <SelectContent align="end">
	                      <SelectItem value="compare">仅对比（不走 LLM）</SelectItem>
	                      <SelectItem value="policy_meeting">严格（少改动）</SelectItem>
	                      <SelectItem value="policy_meeting_v2">平衡（推荐）</SelectItem>
	                      <SelectItem value="policy_meeting_aggressive">激进（尽量纠错）</SelectItem>
	                    </SelectContent>
	                  </Select>
                  <Button
                    variant="outline"
                    onClick={handleDownloadEnhancedAudio}
                    disabled={files.length !== 1 || isTranscribing || isEnhancing || !preprocess.clearvoice_denoise_enable}
                    title="使用当前预处理配置（如 ClearVoice）生成增强后的 WAV 并下载"
                  >
                    {isEnhancing ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        处理中...
                      </>
                    ) : (
                      <>
                        <Download className="h-4 w-4 mr-2" />
                        降噪音频
                      </>
                    )}
                  </Button>
                  {isTranscribing && (
                    <Button variant="outline" onClick={handleCancel} title="取消当前请求（不会影响服务端已在跑的推理）">
                      取消
                    </Button>
                  )}
                  {files.length > 0 && !isTranscribing && (
                    <Button variant="outline" onClick={handleClear}>
                      清空
                    </Button>
                  )}
                </div>

                {isTranscribing && (
                  <div className="rounded-md border bg-muted/20 p-3">
                    {transcribePhase === 'uploading' && uploadProgress !== null ? (
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span>上传中...</span>
                          <span className="tabular-nums">{uploadProgress}%</span>
                        </div>
                        <Progress value={uploadProgress} />
                        <p className="text-xs text-muted-foreground">
                          上传完成后会进入推理阶段；全量优化可能需要几分钟。
                        </p>
                      </div>
                    ) : (
                      <div className="flex items-start gap-3">
                        <CircularProgressIndeterminate size="sm" />
                        <div className="flex-1">
                          <p className="text-sm font-medium">处理中...</p>
	                          <p className="text-xs text-muted-foreground">
	                            全量优化会并发调用多个模型
	                            {ensembleOptions.apply_llm ? ' + LLM 融合润色' : '（不使用 LLM）'}
	                            （已用时 {formatElapsed(elapsedMs)}）
	                          </p>
	                        </div>
	                      </div>
	                    )}
                  </div>
                )}

                {/* 长音频/大文件：异步任务队列（避免同步 HTTP 超时） */}
                <div className="rounded-md border bg-muted/10 p-3 space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">长音频队列</p>
                      <p className="text-xs text-muted-foreground truncate">
                        3-4 小时会议建议使用队列：上传后立即返回 task_id，后台处理，完成后可查看/下载。
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      onClick={handleSubmitFileTasks}
                      disabled={files.length === 0 || isSubmittingFile || isTranscribing}
                      title="适合超长音频：提交后在下方任务队列查看进度与结果"
                    >
                      {isSubmittingFile ? (
                        <>
                          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                          提交中...
                        </>
                      ) : (
                        '加入队列'
                      )}
                    </Button>
                  </div>

                  {isSubmittingFile && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span className="truncate">{fileSubmitName ? `上传中：${fileSubmitName}` : '上传中...'}</span>
                        {fileSubmitProgress !== null ? (
                          <span className="tabular-nums">{Math.max(0, Math.min(100, fileSubmitProgress))}%</span>
                        ) : null}
                      </div>
                      {fileSubmitProgress !== null ? <Progress value={fileSubmitProgress} /> : null}
                    </div>
                  )}

                  {fileTasks.length > 0 ? (
                    <TaskManager
                      tasks={fileTasks}
                      onViewResult={(t) => void handleViewAsyncTaskResult(t as AsyncTranscribeTask)}
                      onRemove={handleRemoveAsyncTask}
                      onRefresh={handleRefreshAsyncTask}
                    />
                  ) : null}
                </div>
              </>
            ) : (
              <div className="space-y-4">
                <UrlTranscribe
                  onSubmit={handleUrlSubmit}
                  isLoading={isSubmittingUrl}
                  disabled={isSubmittingUrl}
                />

                <TaskManager
                  tasks={urlTasks}
                  onViewResult={(t) => void handleViewAsyncTaskResult(t as AsyncTranscribeTask)}
                  onRemove={handleRemoveAsyncTask}
                  onRetry={(t) => handleRetryUrlTask(t as AsyncTranscribeTask)}
                  onRefresh={handleRefreshAsyncTask}
                />
              </div>
            )}
          </CardContent>
        </Card>

        {/* 转写选项 */}
        <TranscribeOptions />
      </div>

      {/* 时间轴 */}
      {result && result.sentences.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">时间轴</CardTitle>
          </CardHeader>
          <CardContent>
            <Timeline
              sentences={result.sentences}
              selectedIndex={selectedIndex}
              onSelectSentence={handleSelectSentence}
            />
          </CardContent>
        </Card>
      )}

      {/* 转写结果 */}
      {/* 多模型候选（仅当使用“全量优化”接口时展示） */}
      {result && ensembleResult && (
        <EnsemblePanel ensemble={ensembleResult} />
      )}

      {result && (
        <TranscriptView
          result={result}
          filename={resultFilename}
        />
      )}

      {/* 空状态 */}
      {!result && !isTranscribing && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground text-center">
              上传音频文件并点击"开始转写"按钮，转写结果将显示在这里
            </p>
          </CardContent>
        </Card>
      )}

      {/* 转写历史 */}
      <HistoryList onViewResult={handleViewHistoryItem} />
    </div>
  )
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}
