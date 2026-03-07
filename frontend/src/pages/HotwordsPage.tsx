import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Loader2, RefreshCw, Plus, Upload, Search } from 'lucide-react'
import {
  appendContextHotwords,
  appendHotwords,
  appendRectifyRecord,
  appendRulesText,
  getContextHotwords,
  getHotwords,
  getRectifyText,
  getRulesText,
  reloadContextHotwords,
  reloadHotwords,
  reloadRectifyText,
  reloadRulesText,
  updateContextHotwords,
  updateHotwords,
  updateRectifyText,
  updateRulesText,
} from '@/lib/api'
import { useBackendStore } from '@/stores'

type HotwordsTab = 'forced' | 'context' | 'rules' | 'rectify'

function HotwordsListEditor(props: { mode: 'forced' | 'context' }) {
  const queryClient = useQueryClient()
  const { baseUrl } = useBackendStore()
  const isContextMode = props.mode === 'context'
  const [draftText, setDraftText] = useState('')
  const [isDirty, setIsDirty] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')

  // 获取热词列表
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['hotwords', props.mode, baseUrl],
    queryFn: isContextMode ? getContextHotwords : getHotwords,
  })

  const serverText = (data?.hotwords ?? []).join('\n')
  const editText = isDirty ? draftText : serverText

  // 更新热词
  const updateMutation = useMutation({
    mutationFn: (hotwords: string[]) =>
      isContextMode ? updateContextHotwords(hotwords) : updateHotwords(hotwords),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['hotwords'] })
      setIsDirty(false)
      setDraftText('')
      toast.success(`${isContextMode ? '上下文热词' : '热词'}更新成功，共 ${response.count} 个`)
    },
    onError: () => {
      toast.error(`${isContextMode ? '上下文热词' : '热词'}更新失败`)
    },
  })

  // 追加热词
  const appendMutation = useMutation({
    mutationFn: (hotwords: string[]) =>
      isContextMode ? appendContextHotwords(hotwords) : appendHotwords(hotwords),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['hotwords'] })
      setIsDirty(false)
      setDraftText('')
      toast.success(response.message)
    },
    onError: () => {
      toast.error(`追加${isContextMode ? '上下文热词' : '热词'}失败`)
    },
  })

  // 重载热词
  const reloadMutation = useMutation({
    mutationFn: isContextMode ? reloadContextHotwords : reloadHotwords,
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['hotwords'] })
      setIsDirty(false)
      setDraftText('')
      toast.success(response.message)
    },
    onError: () => {
      toast.error(`重载${isContextMode ? '上下文热词' : '热词'}失败`)
    },
  })

  // 解析编辑框内容为热词数组
  const parseHotwords = (text: string): string[] =>
    text
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'))

  // 更新全部（允许清空）
  const handleUpdate = () => {
    const hotwords = parseHotwords(editText)
    updateMutation.mutate(hotwords)
  }

  // 追加热词
  const handleAppend = () => {
    const hotwords = parseHotwords(editText)
    const existingSet = new Set(data?.hotwords || [])
    const newHotwords = hotwords.filter((hw) => !existingSet.has(hw))

    if (newHotwords.length === 0) {
      toast.info('没有新的热词需要追加')
      return
    }
    appendMutation.mutate(newHotwords)
  }

  // 重载热词
  const handleReload = () => {
    reloadMutation.mutate()
  }

  // 过滤显示的热词
  const filteredHotwords = data?.hotwords.filter((hw) =>
    hw.toLowerCase().includes(searchTerm.toLowerCase())
  )

  const isPending = updateMutation.isPending || appendMutation.isPending || reloadMutation.isPending

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {/* 热词编辑 */}
      <Card>
        <CardHeader>
          <CardTitle>{isContextMode ? '上下文热词编辑' : '强制热词编辑'}</CardTitle>
          <CardDescription>每行一个热词，以 # 开头的行为注释</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            placeholder={
              isContextMode
                ? '输入上下文热词，每行一个...\n# 这是注释\n常州\n政企通\n我的常州\n高效办成一件事\n免申即享\n政务大模型'
                : '输入强制热词，每行一个...\n# 这是注释\n政企通\n我的常州\n常州市数据局\n等保2.0\n一网通办'
            }
            value={editText}
            onChange={(e) => {
              if (!isDirty) setIsDirty(true)
              setDraftText(e.target.value)
            }}
            className="min-h-[300px] font-mono text-sm"
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={handleUpdate} disabled={isPending}>
              {updateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              更新全部
            </Button>
            <Button variant="outline" onClick={handleAppend} disabled={isPending}>
              {appendMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <Plus className="h-4 w-4 mr-2" />
              追加{isContextMode ? '上下文热词' : '热词'}
            </Button>
            <Button variant="outline" onClick={handleReload} disabled={isPending}>
              {reloadMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <Upload className="h-4 w-4 mr-2" />
              从文件重载
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 当前热词 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>当前{isContextMode ? '上下文热词库' : '热词库'}</CardTitle>
              <CardDescription>
                共 <Badge variant="secondary">{data?.count ?? 0}</Badge> 个{isContextMode ? '上下文热词' : '热词'}
              </CardDescription>
            </div>
            <Button variant="ghost" size="icon" onClick={() => refetch()}>
              <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 搜索 */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder={`搜索${isContextMode ? '上下文热词' : '热词'}...`}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9"
            />
          </div>

          {/* 热词列表 */}
          <div className="h-[280px] overflow-y-auto rounded-lg border p-3">
            {isLoading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : filteredHotwords && filteredHotwords.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {filteredHotwords.map((hw, index) => (
                  <Badge key={index} variant="outline">
                    {hw}
                  </Badge>
                ))}
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground">
                {searchTerm ? '没有匹配的热词' : '暂无热词'}
              </div>
            )}
          </div>

          {/* 统计信息 */}
          {searchTerm && filteredHotwords && (
            <p className="text-sm text-muted-foreground text-right">
              显示 {filteredHotwords.length} / {data?.count} 个
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function RulesEditor() {
  const queryClient = useQueryClient()
  const { baseUrl } = useBackendStore()
  const [draftText, setDraftText] = useState('')
  const [isDirty, setIsDirty] = useState(false)
  const [appendText, setAppendText] = useState('')

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['hotfiles', 'rules', baseUrl],
    queryFn: getRulesText,
  })

  const serverText = data?.text ?? ''
  const editText = isDirty ? draftText : serverText

  const updateMutation = useMutation({
    mutationFn: (text: string) => updateRulesText(text),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles', 'rules'] })
      setIsDirty(false)
      setDraftText('')
      toast.success(`${res.message}（${res.count} 条）`)
    },
    onError: () => toast.error('规则更新失败'),
  })

  const appendMutation = useMutation({
    mutationFn: (text: string) => appendRulesText(text),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles', 'rules'] })
      setAppendText('')
      setIsDirty(false)
      setDraftText('')
      toast.success(`${res.message}（当前 ${res.count} 条）`)
    },
    onError: () => toast.error('规则追加失败'),
  })

  const reloadMutation = useMutation({
    mutationFn: reloadRulesText,
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles', 'rules'] })
      setIsDirty(false)
      setDraftText('')
      toast.success(`${res.message}（${res.count} 条）`)
    },
    onError: () => toast.error('规则重载失败'),
  })

  const isPending = updateMutation.isPending || appendMutation.isPending || reloadMutation.isPending

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>规则替换（hot-rules.txt）</CardTitle>
          <CardDescription>每行一条：正则/文本 ` = ` 替换内容（支持 # 注释）</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            placeholder={'毫安时 = mAh\n伏特 = V\n(艾特)\\s*(\\w+)\\s*(点)\\s*(\\w+) = @$2.$4\n'}
            value={editText}
            onChange={(e) => {
              if (!isDirty) setIsDirty(true)
              setDraftText(e.target.value)
            }}
            className="min-h-[320px] font-mono text-sm"
          />

          <div className="flex flex-wrap gap-2">
            <Button onClick={() => updateMutation.mutate(editText)} disabled={isPending}>
              {updateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              更新全部
            </Button>
            <Button variant="outline" onClick={() => reloadMutation.mutate()} disabled={isPending}>
              {reloadMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <Upload className="h-4 w-4 mr-2" />
              从文件重载
            </Button>
          </div>

          <div className="space-y-2">
            <Label htmlFor="rules-append">追加片段</Label>
            <Textarea
              id="rules-append"
              placeholder={'例如：\n赫兹 = Hz\n千兆 = GHz\n'}
              value={appendText}
              onChange={(e) => setAppendText(e.target.value)}
              className="min-h-[90px] font-mono text-sm"
            />
            <div className="flex justify-end">
              <Button
                variant="outline"
                onClick={() => appendMutation.mutate(appendText)}
                disabled={isPending || !appendText.trim()}
              >
                {appendMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                <Plus className="h-4 w-4 mr-2" />
                追加规则
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>当前规则</CardTitle>
              <CardDescription>
                共 <Badge variant="secondary">{data?.count ?? 0}</Badge> 条
              </CardDescription>
            </div>
            <Button variant="ghost" size="icon" onClick={() => refetch()}>
              <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>适合做“单位/格式/符号”的精准替换（强制）。</p>
          <p>提示：建议用 ` = `（左右空格）分隔，避免正则里出现 `=` 时误判。</p>
          <p>想做“相似发音替换”，用强制热词更合适。</p>
        </CardContent>
      </Card>
    </div>
  )
}

function RectifyEditor() {
  const queryClient = useQueryClient()
  const { baseUrl } = useBackendStore()
  const [draftText, setDraftText] = useState('')
  const [isDirty, setIsDirty] = useState(false)
  const [wrong, setWrong] = useState('')
  const [right, setRight] = useState('')

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['hotfiles', 'rectify', baseUrl],
    queryFn: getRectifyText,
  })

  const serverText = data?.text ?? ''
  const editText = isDirty ? draftText : serverText

  const updateMutation = useMutation({
    mutationFn: (text: string) => updateRectifyText(text),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles', 'rectify'] })
      setIsDirty(false)
      setDraftText('')
      toast.success(`${res.message}（${res.count} 条）`)
    },
    onError: () => toast.error('纠错历史更新失败'),
  })

  const appendMutation = useMutation({
    mutationFn: (p: { wrong: string; right: string }) => appendRectifyRecord(p.wrong, p.right),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles', 'rectify'] })
      setWrong('')
      setRight('')
      toast.success(`${res.message}（当前 ${res.count} 条）`)
    },
    onError: () => toast.error('追加纠错记录失败'),
  })

  const reloadMutation = useMutation({
    mutationFn: reloadRectifyText,
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles', 'rectify'] })
      setIsDirty(false)
      setDraftText('')
      toast.success(`${res.message}（${res.count} 条）`)
    },
    onError: () => toast.error('纠错历史重载失败'),
  })

  const isPending = updateMutation.isPending || appendMutation.isPending || reloadMutation.isPending

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>纠错历史（hot-rectify.txt）</CardTitle>
          <CardDescription>
            每条纠错用 `---` 分隔：第一行错句，第二行正句（支持 # 注释）
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            placeholder={'错句示例\n正确示例\n---\n错句2\n正确2\n'}
            value={editText}
            onChange={(e) => {
              if (!isDirty) setIsDirty(true)
              setDraftText(e.target.value)
            }}
            className="min-h-[320px] font-mono text-sm"
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => updateMutation.mutate(editText)} disabled={isPending}>
              {updateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              更新全部
            </Button>
            <Button variant="outline" onClick={() => reloadMutation.mutate()} disabled={isPending}>
              {reloadMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <Upload className="h-4 w-4 mr-2" />
              从文件重载
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>当前纠错记录</CardTitle>
              <CardDescription>
                共 <Badge variant="secondary">{data?.count ?? 0}</Badge> 条
              </CardDescription>
            </div>
            <Button variant="ghost" size="icon" onClick={() => refetch()}>
              <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="rectify-wrong">错句</Label>
            <Input
              id="rectify-wrong"
              placeholder="例如：我们下午三点开会把"
              value={wrong}
              onChange={(e) => setWrong(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="rectify-right">正句</Label>
            <Input
              id="rectify-right"
              placeholder="例如：我们下午三点开会吧"
              value={right}
              onChange={(e) => setRight(e.target.value)}
            />
          </div>
          <div className="flex justify-end">
            <Button
              variant="outline"
              onClick={() => appendMutation.mutate({ wrong, right })}
              disabled={isPending || !wrong.trim() || !right.trim()}
            >
              {appendMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <Plus className="h-4 w-4 mr-2" />
              追加纠错
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            纠错历史会被检索后作为 LLM 提示词的一部分（只提供“建议替换”的知识，不会强制改写）。
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

export default function HotwordsPage() {
  const [tab, setTab] = useState<HotwordsTab>('forced')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">热词与纠错</h1>
        <p className="text-muted-foreground">
          所有热词/规则/纠错历史都可在这里管理（前端操作，后端自动热加载）
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab((v as HotwordsTab) || 'forced')}>
        <TabsList className="flex flex-wrap">
          <TabsTrigger value="forced">强制热词（纠错）</TabsTrigger>
          <TabsTrigger value="context">上下文热词（注入提示）</TabsTrigger>
          <TabsTrigger value="rules">规则替换</TabsTrigger>
          <TabsTrigger value="rectify">纠错历史</TabsTrigger>
        </TabsList>

        <TabsContent value="forced" className="space-y-2">
          <p className="text-sm text-muted-foreground">
            强制热词会进入纠错链路（可能替换相似词）；适合少量高确定性术语。
          </p>
          <HotwordsListEditor mode="forced" />
        </TabsContent>

        <TabsContent value="context" className="space-y-2">
          <p className="text-sm text-muted-foreground">
            上下文热词仅用于提示/注入（不会强制替换）；更适合会议专有名词清单。
          </p>
          <HotwordsListEditor mode="context" />
        </TabsContent>

        <TabsContent value="rules" className="space-y-2">
          <p className="text-sm text-muted-foreground">
            规则替换是“强制”的精确替换，适合单位/格式/符号等。
          </p>
          <RulesEditor />
        </TabsContent>

        <TabsContent value="rectify" className="space-y-2">
          <p className="text-sm text-muted-foreground">
            纠错历史用于“建议替换”的知识增强，配合 LLM 润色效果更好。
          </p>
          <RectifyEditor />
        </TabsContent>
      </Tabs>
    </div>
  )
}
