import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Progress } from '@/components/ui/progress'
import { CircularProgressIndeterminate } from '@/components/ui/circular-progress'
import { Loader2, Play, FileAudio, Link } from 'lucide-react'
import { FileDropzone } from '@/components/upload'
import { TranscribeOptions } from '@/components/transcribe'
import { EnsemblePanel, TranscriptView } from '@/components/transcript'
import { Timeline } from '@/components/timeline'
import { HistoryList } from '@/components/history/HistoryList'
import { UrlTranscribe } from '@/components/url/UrlTranscribe'
import { TaskManager, type Task } from '@/components/task/TaskManager'
import { useTranscriptionStore } from '@/stores'
import { useHistoryStore, type HistoryItem } from '@/stores/historyStore'
import { getApiBaseUrl, getTaskResult, transcribeAudio, transcribeAllModels, transcribeBatch, transcribeUrl } from '@/lib/api'
import { fromAxiosError, getUserFriendlyMessage } from '@/lib/errors'
import type { EnsembleTranscribeResponse, SentenceInfo, TranscribeResponse } from '@/lib/api/types'

type UrlTask = Task & {
  backendBaseUrl: string
  savedToHistory?: boolean
}

const URL_TASK_MAX_POLL_MS = 10 * 60 * 1000

export default function TranscribePage() {
  const {
    files,
    options,
    tempHotwords,
    advancedAsrOptionsText,
    advancedAsrOptionsError,
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
  const [urlTasks, setUrlTasks] = useState<UrlTask[]>([])
  const [isSubmittingUrl, setIsSubmittingUrl] = useState(false)
  const [resultFilename, setResultFilename] = useState<string | undefined>()
  const [ensembleResult, setEnsembleResult] = useState<EnsembleTranscribeResponse | null>(null)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [transcribePhase, setTranscribePhase] = useState<'idle' | 'uploading' | 'processing'>('idle')
  const [elapsedMs, setElapsedMs] = useState(0)

  const urlPollingTimersRef = useRef<Record<string, number>>({})
  const urlPollingStartedAtRef = useRef<Record<string, number>>({})
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

  const stopUrlPolling = useCallback((taskId: string) => {
    const timerId = urlPollingTimersRef.current[taskId]
    if (timerId !== undefined) {
      window.clearTimeout(timerId)
      delete urlPollingTimersRef.current[taskId]
    }
    delete urlPollingStartedAtRef.current[taskId]
  }, [])

  useEffect(() => {
    return () => {
      for (const timerId of Object.values(urlPollingTimersRef.current)) {
        window.clearTimeout(timerId)
      }
      urlPollingTimersRef.current = {}
      urlPollingStartedAtRef.current = {}
    }
  }, [])

  const updateUrlTask = useCallback((taskId: string, updater: (task: UrlTask) => UrlTask) => {
    setUrlTasks((prev) => prev.map((t) => (t.id === taskId ? updater(t) : t)))
  }, [])

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

  const startUrlPolling = useCallback(
    (taskId: string, backendBaseUrl: string) => {
      if (urlPollingTimersRef.current[taskId] !== undefined) {
        return
      }

      urlPollingStartedAtRef.current[taskId] = Date.now()

      const pollOnce = async () => {
        const startedAt = urlPollingStartedAtRef.current[taskId] ?? Date.now()
        if (Date.now() - startedAt > URL_TASK_MAX_POLL_MS) {
          stopUrlPolling(taskId)
          updateUrlTask(taskId, (t) => ({
            ...t,
            status: 'error',
            error: '任务轮询超时，请重试',
          }))
          return
        }

        try {
          const response = await getTaskResult(
            taskId,
            { delete: false },
            { baseURL: backendBaseUrl }
          )

          if (response.status === 'pending' || response.status === 'processing') {
            updateUrlTask(taskId, (t) => ({ ...t, status: response.status }))
            const delayMs = response.status === 'processing' ? 2000 : 1000
            urlPollingTimersRef.current[taskId] = window.setTimeout(pollOnce, delayMs)
            return
          }

          if (response.status === 'success') {
            stopUrlPolling(taskId)
            const data = response.data
            if (!isTranscribeResponse(data)) {
              updateUrlTask(taskId, (t) => ({
                ...t,
                status: 'error',
                error: '任务返回格式异常（缺少转写结果）',
              }))
              return
            }

            updateUrlTask(taskId, (t) => ({
              ...t,
              status: 'success',
              result: data,
            }))
            toast.success('URL 转写完成')

            // Best-effort cleanup: delete the completed task result from the backend cache.
            void getTaskResult(taskId, { delete: true }, { baseURL: backendBaseUrl }).catch(() => {})
            return
          }

          // error
          stopUrlPolling(taskId)
          updateUrlTask(taskId, (t) => ({
            ...t,
            status: 'error',
            error: response.message || '任务失败',
          }))
        } catch (error) {
          stopUrlPolling(taskId)
          updateUrlTask(taskId, (t) => ({
            ...t,
            status: 'error',
            error: error instanceof Error ? error.message : '请求失败',
          }))
        }
      }

      void pollOnce()
    },
    [isTranscribeResponse, stopUrlPolling, updateUrlTask]
  )

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
          asrOptionsText: advancedAsrOptionsText.trim() ? advancedAsrOptionsText : undefined,
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
            sentences: response.sentences,
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
                sentences: r.result.sentences,
                rawText: r.result.raw_text,
                options: {
                  withSpeaker: options.with_speaker,
                  applyHotword: options.apply_hotword,
                  applyLlm: options.apply_llm,
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
        ...options,
        apply_llm: true,
        // Default to the policy role for this button unless user explicitly chose another.
        llm_role:
          options.llm_role && options.llm_role !== 'default'
            ? options.llm_role
            : 'policy_meeting_aggressive',
        hotwords: tempHotwords || undefined,
        asrOptionsText: advancedAsrOptionsText.trim() ? advancedAsrOptionsText : undefined,
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
          sentences: response.final.sentences,
          rawText: response.final.raw_text,
          options: {
            withSpeaker: options.with_speaker,
            applyHotword: options.apply_hotword,
            applyLlm: true,
            llmRole: transcribeOptions.llm_role,
          },
        })
        toast.success('全量融合完成')
      } else {
        toast.error('全量融合失败')
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
      asrOptionsText: advancedAsrOptionsText.trim() ? advancedAsrOptionsText : undefined,
    }
  }, [advancedAsrOptionsText, options, tempHotwords])

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

      const task: UrlTask = {
        id: taskId,
        status: 'pending',
        url,
        filename: filename || taskId,
        createdAt: new Date(),
        backendBaseUrl,
      }

      setUrlTasks((prev) => [task, ...prev])
      toast.success('URL 转写任务已提交')
      startUrlPolling(taskId, backendBaseUrl)
    } catch (error) {
      console.error('URL transcription submit error:', error)
      toast.error('URL 转写提交失败，请检查服务连接')
    } finally {
      setIsSubmittingUrl(false)
    }
  }

  const handleViewUrlTaskResult = (task: UrlTask) => {
    if (!task.result) {
      toast.error('该任务暂无可查看的结果')
      return
    }

    setEnsembleResult(null)
    setResult(task.result)
    setResultFilename((task.filename || task.id).replace(/\.[^/.]+$/, ''))
    setSelectedSentence(null)
    setSelectedIndex(undefined)

    if (!task.savedToHistory) {
      addItem({
        filename: task.filename || task.id,
        text: task.result.text,
        sentences: task.result.sentences,
        rawText: task.result.raw_text,
        options: {
          withSpeaker: options.with_speaker,
          applyHotword: options.apply_hotword,
          applyLlm: options.apply_llm,
          llmRole: options.llm_role,
        },
      })
      updateUrlTask(task.id, (t) => ({ ...t, savedToHistory: true }))
    }

    toast.success('已加载 URL 任务结果')
  }

  const handleRemoveUrlTask = (taskId: string) => {
    stopUrlPolling(taskId)
    setUrlTasks((prev) => prev.filter((t) => t.id !== taskId))
  }

  const handleRefreshUrlTask = (taskId: string) => {
    const task = urlTasks.find((t) => t.id === taskId)
    if (!task) return

    stopUrlPolling(taskId)
    updateUrlTask(taskId, (t) => ({ ...t, status: 'pending', error: undefined }))
    startUrlPolling(taskId, task.backendBaseUrl)
  }

  const handleRetryUrlTask = (task: UrlTask) => {
    if (!task.url) {
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
      sentences: item.sentences,
      raw_text: item.rawText,
    })
    setResultFilename(item.filename.replace(/\.[^/.]+$/, ''))
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
                    title="同时跑全部模型并做 LLM 融合润色（耗时更长）"
                  >
                    全量优化
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
                            全量优化会并发调用多个模型 + LLM 融合润色（已用时 {formatElapsed(elapsedMs)}）
                          </p>
                        </div>
                      </div>
                    )}
                  </div>
                )}
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
                  onViewResult={(t) => handleViewUrlTaskResult(t as UrlTask)}
                  onRemove={handleRemoveUrlTask}
                  onRetry={(t) => handleRetryUrlTask(t as UrlTask)}
                  onRefresh={handleRefreshUrlTask}
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
