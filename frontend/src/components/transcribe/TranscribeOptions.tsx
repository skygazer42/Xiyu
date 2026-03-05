import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { useQuery, useQueries } from '@tanstack/react-query'
import { useBackendStore, useTranscriptionStore } from '@/stores'
import { getBackendInfo, getBackendTargets, probeBackendInfo } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useMemo, useState } from 'react'
import { Sparkles, Users, BookText, Bot, Server, Braces, ChevronDown } from 'lucide-react'

function _formatHostWithPort(hostname: string, port: number): string {
  const h = String(hostname || '').trim() || 'localhost'
  // IPv6 literal needs brackets for URL host.
  const host = h.includes(':') && !h.startsWith('[') ? `[${h}]` : h
  return `${host}:${port}`
}

function _makePresetBaseUrl(port: number): string {
  if (typeof window === 'undefined') {
    return `http://${_formatHostWithPort('localhost', port)}`
  }
  const protocol = String(window.location.protocol || 'http:') || 'http:'
  const hostname = String(window.location.hostname || 'localhost') || 'localhost'
  return `${protocol}//${_formatHostWithPort(hostname, port)}`
}

const PRESET_BACKENDS: Array<{ label: string; value: string; baseUrl: string }> = [
  // Radix Select `value` must be non-empty, so we use a sentinel for relative baseUrl.
  { label: '当前服务 (相对路径)', value: '__relative__', baseUrl: '' },
  { label: 'PyTorch (8101)', value: _makePresetBaseUrl(8101), baseUrl: _makePresetBaseUrl(8101) },
  { label: 'ONNX (8102)', value: _makePresetBaseUrl(8102), baseUrl: _makePresetBaseUrl(8102) },
  { label: 'SenseVoice (8103)', value: _makePresetBaseUrl(8103), baseUrl: _makePresetBaseUrl(8103) },
  { label: 'GGUF (8104)', value: _makePresetBaseUrl(8104), baseUrl: _makePresetBaseUrl(8104) },
  { label: 'Whisper (8105)', value: _makePresetBaseUrl(8105), baseUrl: _makePresetBaseUrl(8105) },
  { label: 'Qwen3 (8201)', value: _makePresetBaseUrl(8201), baseUrl: _makePresetBaseUrl(8201) },
  { label: 'VibeVoice (8202)', value: _makePresetBaseUrl(8202), baseUrl: _makePresetBaseUrl(8202) },
  { label: 'Router (8200)', value: _makePresetBaseUrl(8200), baseUrl: _makePresetBaseUrl(8200) },
]

const ROUTER_TARGETS: Array<{ label: string; value: string }> = [
  { label: '自动 (Router 策略)', value: 'auto' },
  { label: 'Qwen3 (远程)', value: 'qwen3' },
  { label: 'VibeVoice (远程)', value: 'vibevoice' },
  { label: 'PyTorch (内网容器)', value: 'pytorch' },
  { label: 'ONNX (内网容器)', value: 'onnx' },
  { label: 'SenseVoice (内网容器)', value: 'sensevoice' },
  { label: 'GGUF (内网容器)', value: 'gguf' },
  { label: 'Whisper (内网容器)', value: 'whisper' },
]

export function TranscribeOptions() {
  const {
    options,
    setOptions,
    ensembleOptions,
    setEnsembleOptions,
    tempHotwords,
    setTempHotwords,
    advancedAsrOptionsText,
    setAdvancedAsrOptionsText,
    advancedAsrOptionsError,
    setAdvancedAsrOptionsError,
    preprocess,
    setPreprocess,
  } = useTranscriptionStore()
  const { baseUrl, setBaseUrl } = useBackendStore()
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [backendSelectOpen, setBackendSelectOpen] = useState(false)
  const [routerTargetSelectOpen, setRouterTargetSelectOpen] = useState(false)

  const backendOptions = useMemo(() => {
    if (!baseUrl || PRESET_BACKENDS.some((b) => b.value === baseUrl)) {
      return PRESET_BACKENDS
    }
    return [{ label: `自定义: ${baseUrl}`, value: baseUrl, baseUrl }, ...PRESET_BACKENDS]
  }, [baseUrl])

  const selectedBackendValue = useMemo(() => {
    const hit = backendOptions.find((b) => b.baseUrl === baseUrl)
    return hit?.value || '__relative__'
  }, [backendOptions, baseUrl])

  const backendInfoQuery = useQuery({
    queryKey: ['backendInfo', baseUrl],
    queryFn: getBackendInfo,
    retry: false,
    staleTime: 30000,
  })

  const isRouter = backendInfoQuery.data?.backend === 'router'

  const backendTargetsQuery = useQuery({
    queryKey: ['backendTargets', baseUrl],
    queryFn: getBackendTargets,
    enabled: Boolean(isRouter) && routerTargetSelectOpen,
    retry: false,
    staleTime: 0,
  })

  const backendProbeQueries = useQueries({
    queries: backendOptions.map((b) => ({
      queryKey: ['backendProbe', b.baseUrl || '__relative__'],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        probeBackendInfo(b.baseUrl, { timeoutMs: 1500, signal }),
      enabled: backendSelectOpen,
      retry: false,
      staleTime: 0,
    })),
  })

  const getProbeStatus = (idx: number): 'ok' | 'loading' | 'error' | 'idle' => {
    const q = backendProbeQueries[idx]
    if (!backendSelectOpen) return 'idle'
    if (q.isPending) return 'loading'
    if (q.isError) return 'error'
    if (q.isSuccess) return 'ok'
    return 'idle'
  }

  const getProbeLabel = (idx: number): string => {
    const q = backendProbeQueries[idx]
    if (q.isSuccess) {
      const name = String((q.data?.info as Record<string, unknown> | undefined)?.name || q.data?.backend || '')
      return name ? `可用 · ${name}` : '可用'
    }
    if (q.isPending) return '探测中'
    if (q.isError) return '不可用'
    return ''
  }

  const supportsSpeaker = backendInfoQuery.data?.capabilities.supports_speaker
  const supportsSpeakerFallback = backendInfoQuery.data?.capabilities.supports_speaker_fallback
  const speakerStrategy = backendInfoQuery.data?.capabilities.speaker_strategy
  const advancedEnabled = advancedAsrOptionsText.trim().length > 0

  const targetStatusByKey = useMemo(() => {
    const m = new Map<string, { ok: boolean; name?: string; error?: string }>()
    const targets = backendTargetsQuery.data?.targets
    if (!Array.isArray(targets)) return m

    targets.forEach((t) => {
      if (!t || typeof t !== 'object') return
      const key = String((t as { key?: unknown }).key || '').trim().toLowerCase()
      if (!key) return
      const ok = Boolean((t as { ok?: unknown }).ok)
      const info = (t as { info?: unknown }).info
      const name =
        info && typeof info === 'object' && !Array.isArray(info)
          ? String((info as Record<string, unknown>).name || '')
          : ''
      const error = String((t as { error?: unknown }).error || '').trim()
      m.set(key, { ok, name: name || undefined, error: error || undefined })
    })
    return m
  }, [backendTargetsQuery.data])

  const getTargetProbeLabel = (key: string): string => {
    if (!routerTargetSelectOpen) return ''
    if (backendTargetsQuery.isPending) return '探测中'
    if (backendTargetsQuery.isError) return '不可用'
    const st = targetStatusByKey.get(key)
    if (!st) return ''
    if (st.ok) return st.name ? `可用 · ${st.name}` : '可用'
    return st.error ? `不可用 · ${st.error}` : '不可用'
  }

  const getTargetProbeStatus = (key: string): 'ok' | 'loading' | 'error' | 'idle' => {
    if (!routerTargetSelectOpen) return 'idle'
    if (backendTargetsQuery.isPending) return 'loading'
    if (backendTargetsQuery.isError) return 'error'
    const st = targetStatusByKey.get(key)
    if (!st) return 'idle'
    return st.ok ? 'ok' : 'error'
  }

  const applyAsrOptionsTemplate = (template: Record<string, unknown>) => {
    setAdvancedAsrOptionsText(JSON.stringify(template, null, 2))
    setAdvancedAsrOptionsError(null)
    setAdvancedOpen(true)
  }

  const formatAdvancedAsrOptions = () => {
    const s = advancedAsrOptionsText.trim()
    if (!s) {
      return
    }
    try {
      const obj: unknown = JSON.parse(s)
      if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
        setAdvancedAsrOptionsError('必须是 JSON 对象（例如 {"postprocess": {...}}）')
        return
      }
      setAdvancedAsrOptionsText(JSON.stringify(obj, null, 2))
      setAdvancedAsrOptionsError(null)
    } catch (e) {
      setAdvancedAsrOptionsError(e instanceof Error ? e.message : 'JSON 解析失败')
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>转写选项</CardTitle>
        <CardDescription>配置转写参数</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 后端选择 */}
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <Server className="h-5 w-5 text-muted-foreground" />
            <div className="flex-1">
              <Label htmlFor="backend" className="text-base">后端</Label>
              <p className="text-sm text-muted-foreground">选择本次转写使用的服务地址</p>
            </div>
            {backendInfoQuery.isLoading ? (
              <Badge variant="outline">探测中</Badge>
            ) : backendInfoQuery.isError ? (
              <Badge variant="outline" className="border-red-200 text-red-700 bg-red-500/5">
                未连接
              </Badge>
            ) : (
              <div className="flex items-center gap-2">
                <Badge variant="outline">
                  {String(backendInfoQuery.data?.info?.name || backendInfoQuery.data?.backend || 'backend')}
                </Badge>
                <Badge
                  variant="outline"
                  className={
                    speakerStrategy === 'native'
                      ? 'border-green-200 text-green-700 bg-green-500/5'
                      : speakerStrategy === 'external'
                        ? 'border-purple-200 text-purple-700 bg-purple-500/5'
                        : speakerStrategy === 'fallback_diarization' || speakerStrategy === 'fallback_backend'
                          ? 'border-blue-200 text-blue-700 bg-blue-500/5'
                          : speakerStrategy === 'error'
                            ? 'border-red-200 text-red-700 bg-red-500/5'
                            : 'border-amber-200 text-amber-700 bg-amber-500/5'
                  }
                >
                  {speakerStrategy === 'native'
                    ? '支持说话人'
                    : speakerStrategy === 'external'
                      ? 'external 说话人'
                      : speakerStrategy === 'fallback_diarization'
                        ? 'fallback 说话人'
                        : speakerStrategy === 'fallback_backend'
                          ? '回退后端说话人'
                          : speakerStrategy === 'ignore'
                            ? '忽略说话人'
                            : speakerStrategy === 'error'
                              ? '说话人会报错'
                              : supportsSpeaker
                                ? '支持说话人'
                                : supportsSpeakerFallback
                                  ? 'fallback 说话人'
                                  : '不支持说话人'}
                </Badge>
              </div>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="backend">快速选择</Label>
            <Select
              value={selectedBackendValue}
              onOpenChange={setBackendSelectOpen}
              onValueChange={(value) => {
                const hit = backendOptions.find((b) => b.value === value)
                const nextBaseUrl = hit ? hit.baseUrl : value
                setBaseUrl(nextBaseUrl)
              }}
            >
              <SelectTrigger id="backend">
                <SelectValue placeholder="选择后端..." />
              </SelectTrigger>
              <SelectContent>
                {backendOptions.map((b, idx) => (
                  <SelectItem key={b.value} value={b.value}>
                    <span className="flex w-full items-center justify-between gap-2">
                      <span className="truncate">{b.label}</span>
                      {backendSelectOpen ? (
                        <span
                          className={cn(
                            'text-xs',
                            getProbeStatus(idx) === 'ok'
                              ? 'text-green-700 dark:text-green-400'
                              : getProbeStatus(idx) === 'error'
                                ? 'text-red-700 dark:text-red-400'
                                : 'text-muted-foreground'
                          )}
                        >
                          {getProbeLabel(idx)}
                        </span>
                      ) : null}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <p className="text-xs text-muted-foreground">
              当前 API：{baseUrl ? baseUrl : '相对路径（同源部署）'}
            </p>
          </div>
        </div>

        {/* Router 目标后端（单端口下仍可选模型） */}
        {isRouter ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <Server className="h-5 w-5 text-muted-foreground" />
              <div className="flex-1">
                <Label htmlFor="router-target" className="text-base">目标模型（Router）</Label>
                <p className="text-sm text-muted-foreground">对外只暴露 1 个端口，但内部按需转发到模型容器</p>
              </div>
              {backendTargetsQuery.isPending ? (
                <Badge variant="outline">探测中</Badge>
              ) : backendTargetsQuery.isError ? (
                <Badge variant="outline" className="border-amber-200 text-amber-700 bg-amber-500/5">
                  未探测
                </Badge>
              ) : backendTargetsQuery.isSuccess ? (
                <Badge variant="outline" className="border-green-200 text-green-700 bg-green-500/5">
                  可选
                </Badge>
              ) : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="router-target">选择模型</Label>
              <Select
                value={(options.target_backend || 'auto').toLowerCase()}
                onOpenChange={setRouterTargetSelectOpen}
                onValueChange={(value) => setOptions({ target_backend: value })}
              >
                <SelectTrigger id="router-target">
                  <SelectValue placeholder="auto" />
                </SelectTrigger>
                <SelectContent>
                  {!ROUTER_TARGETS.some((t) => t.value === (options.target_backend || 'auto').toLowerCase()) ? (
                    <SelectItem value={(options.target_backend || 'auto').toLowerCase()}>
                      自定义: {(options.target_backend || 'auto').toLowerCase()}
                    </SelectItem>
                  ) : null}
                  {ROUTER_TARGETS.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      <span className="flex w-full items-center justify-between gap-2">
                        <span className="truncate">{t.label}</span>
                        {routerTargetSelectOpen ? (
                          <span
                            className={cn(
                              'text-xs',
                              getTargetProbeStatus(t.value) === 'ok'
                                ? 'text-green-700 dark:text-green-400'
                                : getTargetProbeStatus(t.value) === 'error'
                                  ? 'text-red-700 dark:text-red-400'
                                  : 'text-muted-foreground'
                            )}
                          >
                            {getTargetProbeLabel(t.value)}
                          </span>
                        ) : null}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <p className="text-xs text-muted-foreground">
                建议：`auto` 让 Router 根据「短音频/长音频/说话人」自动路由；也可强制固定到某个模型用于 A/B 对比。
              </p>
            </div>
          </div>
        ) : null}

        {/* 音频增强：ClearVoice 降噪（可选） */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Sparkles className="h-5 w-5 text-muted-foreground" />
            <div>
              <Label htmlFor="clearvoice-denoise" className="text-base">ClearVoice 降噪</Label>
              <p className="text-sm text-muted-foreground">先降噪再识别（更准但更慢）</p>
            </div>
          </div>
          <Switch
            id="clearvoice-denoise"
            checked={preprocess.clearvoice_denoise_enable}
            onCheckedChange={(checked) => setPreprocess({ clearvoice_denoise_enable: checked })}
          />
        </div>
        {preprocess.clearvoice_denoise_enable ? (
          <p className="text-xs text-muted-foreground ml-8">
            需要服务端安装 ClearerVoice-Studio 依赖或挂载 `CLEARVOICE_STUDIO_DIR`；否则任务会失败。
          </p>
        ) : null}

        {/* 说话人识别 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Users className="h-5 w-5 text-muted-foreground" />
            <div>
              <Label htmlFor="speaker" className="text-base">说话人识别</Label>
              <p className="text-sm text-muted-foreground">区分不同说话人</p>
            </div>
          </div>
          <Switch
            id="speaker"
            checked={options.with_speaker}
            onCheckedChange={(checked) => setOptions({ with_speaker: checked })}
          />
        </div>

        {options.with_speaker && (
          <div className="ml-8 space-y-3">
            <div className="space-y-2">
              <Label htmlFor="speaker-label-style">说话人标签风格</Label>
              <Select
                value={options.speaker_label_style || 'numeric'}
                onValueChange={(value) =>
                  setOptions({ speaker_label_style: value as typeof options.speaker_label_style })
                }
              >
                <SelectTrigger id="speaker-label-style">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="numeric">数字 (说话人1/2/3)</SelectItem>
                  <SelectItem value="zh">中文 (说话人甲/乙/丙)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {speakerStrategy && speakerStrategy !== 'native' && (
              <p className="text-xs text-muted-foreground">
                {speakerStrategy === 'external'
                  ? '将使用 external diarizer 生成说话人 turn；若 diarizer 不可用会自动降级。可在 diarizer 容器设置 DIARIZER_NUM_SPEAKERS（或 DIARIZER_MIN/MAX_SPEAKERS）提升稳定性。'
                  : speakerStrategy === 'fallback_diarization'
                    ? '当前后端原生不支持说话人识别；将使用辅助服务生成分段并按 turn 转写。'
                    : speakerStrategy === 'fallback_backend'
                      ? '当前后端不支持说话人识别；将回退到 PyTorch 后端执行说话人转写。'
                    : speakerStrategy === 'ignore'
                      ? '当前后端不支持说话人识别，将自动忽略该开关。'
                    : speakerStrategy === 'error'
                      ? '当前后端不支持说话人识别；开启该开关会报错。'
                      : supportsSpeakerFallback
                        ? '当前后端原生不支持说话人识别；已启用 fallback（需要辅助服务可用）。'
                        : '当前后端不支持说话人识别，将自动忽略该开关。'}
              </p>
            )}
          </div>
        )}

        {/* 热词纠错 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BookText className="h-5 w-5 text-muted-foreground" />
            <div>
              <Label htmlFor="hotword" className="text-base">热词纠错</Label>
              <p className="text-sm text-muted-foreground">使用热词库进行纠错</p>
            </div>
          </div>
          <Switch
            id="hotword"
            checked={options.apply_hotword}
            onCheckedChange={(checked) => setOptions({ apply_hotword: checked })}
          />
        </div>

        {/* LLM 润色 */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Sparkles className="h-5 w-5 text-muted-foreground" />
              <div>
                <Label htmlFor="llm" className="text-base">LLM 润色（单模型）</Label>
                <p className="text-sm text-muted-foreground">影响「开始转写 / 批量 / URL转写」</p>
              </div>
            </div>
            <Switch
              id="llm"
              checked={options.apply_llm}
              onCheckedChange={(checked) => setOptions({ apply_llm: checked })}
            />
          </div>

          {options.apply_llm && (
            <div className="ml-8 space-y-2">
              <Label htmlFor="llm-role" className="flex items-center gap-2">
                <Bot className="h-4 w-4" />
                LLM 角色
              </Label>
              <Select
                value={options.llm_role}
                onValueChange={(value) => setOptions({ llm_role: value as typeof options.llm_role })}
              >
                <SelectTrigger id="llm-role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="policy_polish_strict">政务听记（严格）</SelectItem>
                  <SelectItem value="policy_polish_balanced">政务听记（平衡）</SelectItem>
                  <SelectItem value="policy_polish_aggressive">政务听记（激进）</SelectItem>
                  <SelectItem value="meeting">通用：会议（最小改动）</SelectItem>
                  <SelectItem value="corrector">通用：专业校对</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
        </div>

        {/* 全量优化（多模型融合） */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Sparkles className="h-5 w-5 text-muted-foreground" />
              <div>
                <Label htmlFor="ensemble-llm" className="text-base">LLM 融合（全量优化）</Label>
                <p className="text-sm text-muted-foreground">仅影响「全量优化」按钮</p>
              </div>
            </div>
            <Switch
              id="ensemble-llm"
              checked={ensembleOptions.apply_llm}
              onCheckedChange={(checked) => setEnsembleOptions({ apply_llm: checked })}
            />
          </div>

          <div className="ml-8 space-y-3">
            {ensembleOptions.apply_llm && (
              <div className="space-y-2">
                <Label htmlFor="ensemble-llm-role" className="flex items-center gap-2">
                  <Bot className="h-4 w-4" />
                  融合提示词
                </Label>
                <Select
                  value={ensembleOptions.llm_role}
                  onValueChange={(value) =>
                    setEnsembleOptions({ llm_role: value as typeof ensembleOptions.llm_role })
                  }
                >
                  <SelectTrigger id="ensemble-llm-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="policy_meeting">政策听记（严格）</SelectItem>
                    <SelectItem value="policy_meeting_v2">政策听记（平衡）</SelectItem>
                    <SelectItem value="policy_meeting_aggressive">政策听记（激进）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="flex items-center justify-between">
              <div>
                <Label htmlFor="ensemble-include-srt" className="text-sm">返回 SRT 字幕</Label>
                <p className="text-xs text-muted-foreground">响应里 `final.srt` 会更大，但方便下载/导出</p>
              </div>
              <Switch
                id="ensemble-include-srt"
                checked={ensembleOptions.include_srt}
                onCheckedChange={(checked) => setEnsembleOptions({ include_srt: checked })}
              />
            </div>
          </div>
        </div>

        {/* 临时热词 */}
        <div className="space-y-2">
          <Label htmlFor="temp-hotwords">临时热词</Label>
          <Textarea
            id="temp-hotwords"
            placeholder="输入临时热词，用空格分隔..."
            value={tempHotwords}
            onChange={(e) => setTempHotwords(e.target.value)}
            className="min-h-[80px] resize-none"
          />
          <p className="text-xs text-muted-foreground">
            临时热词仅对本次转写有效，不会保存到热词库
          </p>
        </div>

        {/* 高级 asr_options */}
        <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
          <CollapsibleTrigger className="flex items-center justify-between w-full p-2 rounded-lg hover:bg-muted transition-colors">
            <div className="flex items-center gap-3">
              <Braces className="h-5 w-5 text-muted-foreground" />
              <div className="text-left">
                <div className="flex items-center gap-2">
                  <span className="text-base font-medium">高级 asr_options</span>
                  {advancedAsrOptionsError ? (
                    <Badge
                      variant="outline"
                      className="border-red-200 text-red-700 bg-red-500/5"
                    >
                      JSON 无效
                    </Badge>
                  ) : advancedEnabled ? (
                    <Badge
                      variant="outline"
                      className="border-green-200 text-green-700 bg-green-500/5"
                    >
                      已启用
                    </Badge>
                  ) : null}
                </div>
                <p className="text-sm text-muted-foreground">
                  直接透传 JSON：分块/后处理/说话人格式（后端会严格校验 allowlist）
                </p>
              </div>
            </div>

            <ChevronDown
              className={cn(
                'h-4 w-4 text-muted-foreground transition-transform',
                !advancedOpen && '-rotate-90'
              )}
            />
          </CollapsibleTrigger>

          <CollapsibleContent className="ml-8 pt-2 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={() =>
                  applyAsrOptionsTemplate({
                    postprocess: {
                      acronym_merge_enable: true,
                      spacing_cjk_ascii_enable: true,
                      punc_convert_enable: true,
                      punc_merge_enable: true,
                    },
                  })
                }
              >
                Qwen3 强后处理
              </Button>
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={() =>
                  applyAsrOptionsTemplate({
                    chunking: {
                      strategy: 'silence',
                      max_chunk_duration_s: 20,
                      min_chunk_duration_s: 5,
                      overlap_duration_s: 1.0,
                      silence_threshold_db: -38,
                      min_silence_duration_s: 0.35,
                      boundary_reconcile_enable: true,
                      boundary_reconcile_window_s: 1.5,
                      max_workers: 2,
                      overlap_chars: 20,
                    },
                  })
                }
              >
                长音频 准确率优先
              </Button>
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={() => {
                  setOptions({ with_speaker: true, speaker_label_style: 'numeric' })
                  applyAsrOptionsTemplate({
                    speaker: {
                      label_style: 'numeric',
                      turn_merge_enable: true,
                      turn_merge_gap_ms: 800,
                      turn_merge_min_chars: 1,
                    },
                    postprocess: {
                      acronym_merge_enable: true,
                      spacing_cjk_ascii_enable: true,
                      punc_convert_enable: true,
                      punc_merge_enable: true,
                    },
                  })
                }}
              >
                会议（准确率优先）
              </Button>

              <div className="ml-auto flex items-center gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={formatAdvancedAsrOptions}
                  disabled={!advancedEnabled}
                >
                  格式化
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => {
                    setAdvancedAsrOptionsText('')
                    setAdvancedAsrOptionsError(null)
                  }}
                  disabled={!advancedEnabled && !advancedAsrOptionsError}
                >
                  清空
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <Textarea
                value={advancedAsrOptionsText}
                onChange={(e) => {
                  const next = e.target.value
                  setAdvancedAsrOptionsText(next)

                  const s = next.trim()
                  if (!s) {
                    setAdvancedAsrOptionsError(null)
                    return
                  }
                  try {
                    const obj: unknown = JSON.parse(s)
                    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
                      setAdvancedAsrOptionsError('必须是 JSON 对象（例如 {"postprocess": {...}}）')
                      return
                    }
                    setAdvancedAsrOptionsError(null)
                  } catch (e) {
                    setAdvancedAsrOptionsError(e instanceof Error ? e.message : 'JSON 解析失败')
                  }
                }}
                placeholder='例如：{"postprocess":{"acronym_merge_enable":true}}'
                className="min-h-[160px] font-mono text-xs"
              />

              {advancedAsrOptionsError && (
                <p className="text-xs text-destructive">
                  JSON 无效：{advancedAsrOptionsError}
                </p>
              )}

              <p className="text-xs text-muted-foreground">
                支持字段：<code className="font-mono">preprocess</code> /{' '}
                <code className="font-mono">chunking</code> /{' '}
                <code className="font-mono">postprocess</code> /{' '}
                <code className="font-mono">speaker</code> /{' '}
                <code className="font-mono">backend</code> /{' '}
                <code className="font-mono">debug</code>。
              </p>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  )
}
