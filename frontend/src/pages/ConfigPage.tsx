import { useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Loader2, RefreshCw, Save } from 'lucide-react'
import { getAllConfig, updateConfig, reloadEngine } from '@/lib/api'
import { useBackendStore, useConfigStore } from '@/stores'
import { ConfigPresets } from '@/components/config/ConfigPresets'

// 配置项定义
interface ConfigItemDef {
  key: string
  label: string
  description: string
  type: 'switch' | 'select' | 'slider' | 'input'
  options?: { value: string; label: string }[]
  min?: number
  max?: number
  step?: number
}

const CORRECTION_CONFIGS: ConfigItemDef[] = [
  { key: 'text_correct_enable', label: '启用文本纠错', description: '开启后将对转写结果进行纠错', type: 'switch' },
  {
    key: 'text_correct_backend', label: '纠错后端', description: '选择纠错引擎', type: 'select',
    options: [
      { value: 'kenlm', label: 'KenLM (轻量/快速)' },
      { value: 'macbert', label: 'MacBERT (效果更强)' },
    ]
  },
  { key: 'confidence_threshold', label: '置信度阈值', description: '低于此阈值的结果触发纠错', type: 'slider', min: 0, max: 1, step: 0.05 },
]

const HOTWORD_CONFIGS: ConfigItemDef[] = [
  { key: 'hotword_injection_enable', label: '启用热词注入', description: '自动将热词注入到 ASR 模型', type: 'switch' },
  { key: 'hotwords_threshold', label: '热词匹配阈值', description: '热词纠错的相似度阈值', type: 'slider', min: 0.5, max: 1, step: 0.05 },
  { key: 'hotword_injection_max', label: '最大注入热词数', description: '单次请求最多注入的热词数量', type: 'slider', min: 0, max: 100, step: 10 },
]

const LLM_CONFIGS: ConfigItemDef[] = [
  { key: 'llm_enable', label: '启用 LLM 润色', description: '使用大语言模型优化转写结果', type: 'switch' },
  { key: 'meeting_overview_enable', label: '会议概览', description: '转写完成后自动生成 2-5 段政务口径概览（需启用 LLM）', type: 'switch' },
  {
    key: 'llm_role', label: 'LLM 角色', description: '选择 LLM 的处理角色', type: 'select',
    options: [
      { value: 'default', label: '默认 (通用纠错)' },
      { value: 'translator', label: '翻译助手' },
      { value: 'code', label: '代码助手' },
      { value: 'corrector', label: '专业校对' },
    ]
  },
  { key: 'llm_fulltext_enable', label: '全文 LLM 模式', description: '对完整文本进行 LLM 处理', type: 'switch' },
  { key: 'llm_batch_size', label: '批量大小', description: 'LLM 批处理的句子数量', type: 'slider', min: 1, max: 20, step: 1 },
  { key: 'llm_context_sentences', label: '上下文句子数', description: '每次处理时包含的上下文句子数', type: 'slider', min: 0, max: 10, step: 1 },
]

const POSTPROCESS_CONFIGS: ConfigItemDef[] = [
  { key: 'filler_remove_enable', label: '移除语气词', description: '移除"嗯"、"呃"等语气词', type: 'switch' },
  { key: 'filler_aggressive', label: '激进模式', description: '更激进地移除填充词', type: 'switch' },
  { key: 'itn_enable', label: '启用 ITN', description: '逆文本归一化 (数字、日期转换)', type: 'switch' },
  { key: 'itn_erhua_remove', label: '移除儿化', description: '将“那边儿”转为“那边”', type: 'switch' },
  { key: 'qj2bj_enable', label: '全角转半角', description: '将全角字符转换为半角', type: 'switch' },
  { key: 'spacing_cjk_ascii_enable', label: '中英混排空格', description: '在中文与英文/数字之间自动加空格', type: 'switch' },
  { key: 'acronym_merge_enable', label: '字母拼写合并', description: '将 "A I" 合并为 "AI"（服务器端处理）', type: 'switch' },
  { key: 'spoken_punc_enable', label: '口述标点指令', description: '在句首/句尾说“逗号/句号/回车”自动转换', type: 'switch' },
  { key: 'punc_convert_enable', label: '标点转换', description: '将全角标点转换为半角等格式化', type: 'switch' },
  { key: 'punc_add_space', label: '标点后加空格', description: '标点后自动添加空格（英文/代码更友好）', type: 'switch' },
  { key: 'punc_restore_enable', label: '标点恢复', description: '自动添加标点符号', type: 'switch' },
  { key: 'punc_merge_enable', label: '标点合并', description: '清理重复/混合标点（例如 “，，，”）', type: 'switch' },
  { key: 'trash_punc_enable', label: '移除末尾标点', description: '实时听写场景：去除末尾“逗号/句号”', type: 'switch' },
  { key: 'trash_punc_chars', label: '末尾标点集合', description: '要移除的末尾标点字符（例如：，。,.)', type: 'input' },
  { key: 'zh_convert_enable', label: '简繁转换', description: '启用中文简繁体转换', type: 'switch' },
  {
    key: 'zh_convert_locale', label: '转换目标', description: '选择转换的目标字体', type: 'select',
    options: [
      { value: 'zh-hans', label: '简体中文 (zh-hans)' },
      { value: 'zh-hant', label: '繁体中文 (zh-hant)' },
      { value: 'zh-tw', label: '繁体中文 (台湾)' },
      { value: 'zh-hk', label: '繁体中文 (香港)' },
    ]
  },
]

const AUDIO_CONFIGS: ConfigItemDef[] = [
  { key: 'audio_normalize_enable', label: '音频归一化', description: '标准化音频音量', type: 'switch' },
  { key: 'audio_denoise_enable', label: '启用降噪', description: '对音频进行降噪处理', type: 'switch' },
  {
    key: 'audio_denoise_backend', label: '降噪后端', description: '选择降噪算法', type: 'select',
    options: [
      { value: 'noisereduce', label: 'NoiseReduce' },
      { value: 'deepfilter3', label: 'DeepFilterNet v3' },
    ]
  },
  { key: 'audio_vocal_separate_enable', label: '人声分离', description: '从背景音乐中分离人声', type: 'switch' },
  { key: 'audio_trim_silence_enable', label: '裁剪静音', description: '移除音频首尾的静音部分', type: 'switch' },
]

export default function ConfigPage() {
  const queryClient = useQueryClient()
  const { serverConfig, setServerConfig, pendingChanges, setPendingChange, clearPendingChanges } = useConfigStore()
  const { baseUrl } = useBackendStore()

  // 获取配置
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['config', baseUrl],
    queryFn: getAllConfig,
  })

  // 更新配置
  const updateMutation = useMutation({
    mutationFn: (updates: Record<string, unknown>) => updateConfig(updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] })
      clearPendingChanges()
      toast.success('配置已更新')
    },
    onError: () => {
      toast.error('配置更新失败')
    },
  })

  // 重载引擎
  const reloadMutation = useMutation({
    mutationFn: reloadEngine,
    onSuccess: () => {
      toast.success('引擎已重新加载')
    },
    onError: () => {
      toast.error('引擎重载失败')
    },
  })

  // 初始化服务器配置
  useEffect(() => {
    if (data?.config) {
      setServerConfig(data.config)
    }
  }, [data, setServerConfig])

  // 获取配置值（优先使用本地修改）
  const getConfigValue = (key: string) => {
    if (key in pendingChanges) {
      return pendingChanges[key]
    }
    return serverConfig[key]
  }

  // 处理配置变更
  const handleConfigChange = (key: string, value: unknown) => {
    setPendingChange(key, value)
  }

  // 保存配置
  const handleSave = () => {
    if (Object.keys(pendingChanges).length === 0) {
      toast.info('没有需要保存的更改')
      return
    }
    updateMutation.mutate(pendingChanges)
  }

  // 重载引擎
  const handleReload = () => {
    reloadMutation.mutate()
  }

  const hasPendingChanges = Object.keys(pendingChanges).length > 0
  const isPending = updateMutation.isPending || reloadMutation.isPending

  // 渲染配置项
  const renderConfigItem = (item: ConfigItemDef) => {
    const value = getConfigValue(item.key)

    switch (item.type) {
      case 'switch':
        return (
          <div key={item.key} className="flex items-center justify-between py-3">
            <div className="space-y-0.5">
              <Label htmlFor={item.key}>{item.label}</Label>
              <p className="text-sm text-muted-foreground">{item.description}</p>
            </div>
            <Switch
              id={item.key}
              checked={Boolean(value)}
              onCheckedChange={(checked) => handleConfigChange(item.key, checked)}
            />
          </div>
        )

      case 'select':
        return (
          <div key={item.key} className="flex items-center justify-between py-3">
            <div className="space-y-0.5">
              <Label htmlFor={item.key}>{item.label}</Label>
              <p className="text-sm text-muted-foreground">{item.description}</p>
            </div>
            <Select
              value={String(value ?? '')}
              onValueChange={(v) => handleConfigChange(item.key, v)}
            >
              <SelectTrigger id={item.key} className="w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {item.options?.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )

      case 'slider':
        return (
          <div key={item.key} className="space-y-3 py-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor={item.key}>{item.label}</Label>
                <p className="text-sm text-muted-foreground">{item.description}</p>
              </div>
              <span className="font-mono text-sm">{Number(value ?? 0).toFixed(2)}</span>
            </div>
            <Slider
              id={item.key}
              min={item.min}
              max={item.max}
              step={item.step}
              value={[Number(value ?? 0)]}
              onValueChange={([v]) => handleConfigChange(item.key, v)}
            />
          </div>
        )

      case 'input':
        return (
          <div key={item.key} className="flex items-center justify-between py-3">
            <div className="space-y-0.5">
              <Label htmlFor={item.key}>{item.label}</Label>
              <p className="text-sm text-muted-foreground">{item.description}</p>
            </div>
            <Input
              id={item.key}
              value={String(value ?? '')}
              onChange={(e) => handleConfigChange(item.key, e.target.value)}
              className="w-[180px]"
            />
          </div>
        )

      default:
        return null
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">配置管理</h1>
          <p className="text-muted-foreground">管理转写引擎的各项配置</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => refetch()} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button variant="outline" onClick={handleReload} disabled={isPending}>
            {reloadMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            重载引擎
          </Button>
          <Button onClick={handleSave} disabled={!hasPendingChanges || isPending}>
            {updateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            <Save className="h-4 w-4 mr-2" />
            保存 {hasPendingChanges && `(${Object.keys(pendingChanges).length})`}
          </Button>
        </div>
      </div>

      {/* 配置预设 */}
      <ConfigPresets
        currentConfig={serverConfig}
        onApplyPreset={(preset) => {
          Object.entries(preset.config).forEach(([key, value]) => {
            setPendingChange(key, value)
          })
          toast.info(`已应用预设: ${preset.name}，请保存以生效`)
        }}
      />

      <Tabs defaultValue="correction" className="space-y-4">
        <TabsList>
          <TabsTrigger value="correction">纠错配置</TabsTrigger>
          <TabsTrigger value="hotword">热词配置</TabsTrigger>
          <TabsTrigger value="llm">LLM 配置</TabsTrigger>
          <TabsTrigger value="postprocess">后处理</TabsTrigger>
          <TabsTrigger value="audio">音频处理</TabsTrigger>
        </TabsList>

        <TabsContent value="correction">
          <Card>
            <CardHeader>
              <CardTitle>纠错配置</CardTitle>
              <CardDescription>文本纠错相关设置</CardDescription>
            </CardHeader>
            <CardContent className="divide-y">
              {CORRECTION_CONFIGS.map(renderConfigItem)}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="hotword">
          <Card>
            <CardHeader>
              <CardTitle>热词配置</CardTitle>
              <CardDescription>热词纠错相关设置</CardDescription>
            </CardHeader>
            <CardContent className="divide-y">
              {HOTWORD_CONFIGS.map(renderConfigItem)}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="llm">
          <Card>
            <CardHeader>
              <CardTitle>LLM 配置</CardTitle>
              <CardDescription>大语言模型润色相关设置</CardDescription>
            </CardHeader>
            <CardContent className="divide-y">
              {LLM_CONFIGS.map(renderConfigItem)}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="postprocess">
          <Card>
            <CardHeader>
              <CardTitle>后处理配置</CardTitle>
              <CardDescription>文本后处理相关设置</CardDescription>
            </CardHeader>
            <CardContent className="divide-y">
              {POSTPROCESS_CONFIGS.map(renderConfigItem)}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="audio">
          <Card>
            <CardHeader>
              <CardTitle>音频处理配置</CardTitle>
              <CardDescription>音频预处理相关设置</CardDescription>
            </CardHeader>
            <CardContent className="divide-y">
              {AUDIO_CONFIGS.map(renderConfigItem)}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
