"use client"

import * as React from "react"
import {
  Search,
  Plus,
  Trash2,
  Download,
  Upload,
  ChevronDown,
  GripVertical,
  AlertCircle,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { toast } from "@/lib/toast"

export interface HotwordGroup {
  name: string
  hotwords: string[]
  isOpen?: boolean
}

export interface HotwordManagerProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 热词列表 */
  hotwords: string[]
  /** 热词变化回调 */
  onHotwordsChange?: (hotwords: string[]) => void
  /** 是否加载中 */
  isLoading?: boolean
  /** 是否可编辑 */
  editable?: boolean
}

// 解析热词为分组 (# 开头的行作为组名)
function parseHotwordsToGroups(hotwords: string[]): HotwordGroup[] {
  const groups: HotwordGroup[] = []
  let currentGroup: HotwordGroup = { name: '未分组', hotwords: [], isOpen: true }

  for (const word of hotwords) {
    if (word.startsWith('#')) {
      // 保存当前组 (如果有热词)
      if (currentGroup.hotwords.length > 0 || currentGroup.name !== '未分组') {
        groups.push(currentGroup)
      }
      // 开始新组
      currentGroup = {
        name: word.slice(1).trim() || '未命名组',
        hotwords: [],
        isOpen: true,
      }
    } else if (word.trim()) {
      currentGroup.hotwords.push(word.trim())
    }
  }

  // 添加最后一个组
  if (currentGroup.hotwords.length > 0 || groups.length === 0) {
    groups.push(currentGroup)
  }

  return groups
}

// 将分组转换回热词列表
function groupsToHotwords(groups: HotwordGroup[]): string[] {
  const result: string[] = []

  for (const group of groups) {
    if (group.name !== '未分组') {
      result.push(`# ${group.name}`)
    }
    result.push(...group.hotwords)
  }

  return result
}

function HotwordManager({
  className,
  hotwords,
  onHotwordsChange,
  isLoading = false,
  editable = true,
  ...props
}: HotwordManagerProps) {
  const [groups, setGroups] = React.useState<HotwordGroup[]>([])
  const [searchQuery, setSearchQuery] = React.useState('')
  const [selectedWords, setSelectedWords] = React.useState<Set<string>>(new Set())
  const [newWord, setNewWord] = React.useState('')
  const [duplicates, setDuplicates] = React.useState<string[]>([])
  const controlsDisabled = isLoading

  // 解析热词为分组
  React.useEffect(() => {
    setGroups(parseHotwordsToGroups(hotwords))
  }, [hotwords])

  // 检测重复
  React.useEffect(() => {
    const allWords = groups.flatMap((g) => g.hotwords)
    const seen = new Set<string>()
    const dups: string[] = []

    for (const word of allWords) {
      const lower = word.toLowerCase()
      if (seen.has(lower)) {
        dups.push(word)
      }
      seen.add(lower)
    }

    setDuplicates(dups)
  }, [groups])

  // 过滤热词
  const filteredGroups = React.useMemo(() => {
    if (!searchQuery) return groups

    return groups
      .map((group) => ({
        ...group,
        hotwords: group.hotwords.filter((word) =>
          word.toLowerCase().includes(searchQuery.toLowerCase())
        ),
      }))
      .filter((group) => group.hotwords.length > 0)
  }, [groups, searchQuery])

  const totalCount = groups.reduce((acc, g) => acc + g.hotwords.length, 0)

  const handleAddWord = () => {
    if (!newWord.trim()) return

    const updatedGroups = [...groups]
    const lastGroup = updatedGroups[updatedGroups.length - 1] || {
      name: '未分组',
      hotwords: [],
    }

    // 检查重复
    const allWords = groups.flatMap((g) => g.hotwords)
    if (allWords.some((w) => w.toLowerCase() === newWord.toLowerCase())) {
      toast.warning('热词已存在')
      return
    }

    lastGroup.hotwords.push(newWord.trim())
    if (updatedGroups.length === 0) {
      updatedGroups.push(lastGroup)
    }

    setGroups(updatedGroups)
    onHotwordsChange?.(groupsToHotwords(updatedGroups))
    setNewWord('')
    toast.success('热词已添加')
  }

  const handleDeleteWord = (groupIndex: number, wordIndex: number) => {
    const updatedGroups = [...groups]
    const word = updatedGroups[groupIndex].hotwords[wordIndex]
    updatedGroups[groupIndex].hotwords.splice(wordIndex, 1)

    setGroups(updatedGroups)
    onHotwordsChange?.(groupsToHotwords(updatedGroups))
    setSelectedWords((prev) => {
      const next = new Set(prev)
      next.delete(word)
      return next
    })
  }

  const handleDeleteSelected = () => {
    if (selectedWords.size === 0) return

    const updatedGroups = groups.map((group) => ({
      ...group,
      hotwords: group.hotwords.filter((word) => !selectedWords.has(word)),
    }))

    setGroups(updatedGroups)
    onHotwordsChange?.(groupsToHotwords(updatedGroups))
    setSelectedWords(new Set())
    toast.success(`已删除 ${selectedWords.size} 个热词`)
  }

  const handleSelectWord = (word: string, checked: boolean) => {
    setSelectedWords((prev) => {
      const next = new Set(prev)
      if (checked) {
        next.add(word)
      } else {
        next.delete(word)
      }
      return next
    })
  }

  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      const allWords = groups.flatMap((g) => g.hotwords)
      setSelectedWords(new Set(allWords))
    } else {
      setSelectedWords(new Set())
    }
  }

  const handleExport = () => {
    const content = groupsToHotwords(groups).join('\n')
    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `hotwords-${Date.now()}.txt`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('热词已导出')
  }

  const handleImport = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = (e) => {
      const content = e.target?.result as string
      const lines = content.split('\n').filter((line) => line.trim())
      setGroups(parseHotwordsToGroups(lines))
      onHotwordsChange?.(lines)
      toast.success(`已导入 ${lines.length} 行`)
    }
    reader.readAsText(file)
    event.target.value = ''
  }

  const toggleGroup = (index: number) => {
    const updatedGroups = [...groups]
    updatedGroups[index].isOpen = !updatedGroups[index].isOpen
    setGroups(updatedGroups)
  }

  return (
    <Card className={className} {...props}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">
            热词管理 ({totalCount})
          </CardTitle>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleExport} disabled={controlsDisabled}>
              <Download className="h-4 w-4 mr-1" />
              导出
            </Button>
            <Button variant="outline" size="sm" asChild>
              <label
                className={cn(
                  controlsDisabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"
                )}
              >
                <Upload className="h-4 w-4 mr-1" />
                导入
                <input
                  type="file"
                  accept=".txt"
                  className="hidden"
                  onChange={handleImport}
                  disabled={controlsDisabled}
                />
              </label>
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* 搜索框 */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索热词..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
            disabled={controlsDisabled}
          />
        </div>

        {/* 添加热词 */}
        {editable && (
          <div className="flex gap-2">
            <Input
              placeholder="输入新热词"
              value={newWord}
              onChange={(e) => setNewWord(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleAddWord()
                }
              }}
              disabled={controlsDisabled}
            />
            <Button onClick={handleAddWord} disabled={controlsDisabled || !newWord.trim()}>
              <Plus className="h-4 w-4 mr-1" />
              添加
            </Button>
          </div>
        )}

        {/* 重复警告 */}
        {duplicates.length > 0 && (
          <div className="flex items-center gap-2 text-sm text-yellow-600 dark:text-yellow-400">
            <AlertCircle className="h-4 w-4" />
            <span>发现 {duplicates.length} 个重复热词</span>
          </div>
        )}

        {/* 工具栏 */}
        {selectedWords.size > 0 && editable && (
          <div className="flex items-center justify-between p-2 rounded-lg bg-muted">
            <div className="flex items-center gap-2">
              <Checkbox
                checked={selectedWords.size === totalCount}
                onCheckedChange={handleSelectAll}
                disabled={controlsDisabled}
              />
              <span className="text-sm">已选 {selectedWords.size} 个</span>
            </div>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleDeleteSelected}
              disabled={controlsDisabled}
            >
              <Trash2 className="h-4 w-4 mr-1" />
              删除选中
            </Button>
          </div>
        )}

        {/* 热词分组列表 */}
        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {filteredGroups.map((group, groupIndex) => (
            <Collapsible
              key={groupIndex}
              open={group.isOpen}
              onOpenChange={() => toggleGroup(groupIndex)}
            >
              <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 rounded-lg hover:bg-muted transition-colors">
                <ChevronDown
                  className={cn(
                    'h-4 w-4 transition-transform',
                    !group.isOpen && '-rotate-90'
                  )}
                />
                <span className="font-medium">{group.name}</span>
                <Badge variant="secondary" className="ml-auto">
                  {group.hotwords.length}
                </Badge>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="pl-6 pt-1 space-y-1">
                  {group.hotwords.map((word, wordIndex) => (
                    <div
                      key={wordIndex}
                      className={cn(
                        'flex items-center gap-2 p-2 rounded-lg hover:bg-muted/50 group',
                        duplicates.includes(word) && 'bg-yellow-500/10'
                      )}
                    >
                      {editable && (
                        <Checkbox
                          checked={selectedWords.has(word)}
                          onCheckedChange={(checked) =>
                            handleSelectWord(word, checked === true)
                          }
                          disabled={controlsDisabled}
                        />
                      )}
                      <GripVertical className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 cursor-grab" />
                      <span
                        className={cn(
                          'flex-1',
                          searchQuery &&
                            word.toLowerCase().includes(searchQuery.toLowerCase()) &&
                            'bg-yellow-200 dark:bg-yellow-800'
                        )}
                      >
                        {word}
                      </span>
                      {editable && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 opacity-0 group-hover:opacity-100"
                          onClick={() => handleDeleteWord(groupIndex, wordIndex)}
                          disabled={controlsDisabled}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </CollapsibleContent>
            </Collapsible>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export { HotwordManager }
