import { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Copy, Check, AlignLeft, List, Users } from 'lucide-react'
import { SentenceList } from './SentenceList'
import { SpeakerStats } from './SpeakerStats'
import { SpeakerBadge } from './SpeakerBadge'
import { ExportMenu } from './ExportMenu'
import { useTranscriptionStore } from '@/stores'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { appendRectifyRecord, appendRulesText } from '@/lib/api/hotwords'
import { toast } from '@/lib/toast'
import type { SpeakerTurn, TranscribeResponse, SentenceInfo } from '@/lib/api/types'

interface TranscriptViewProps {
  result: TranscribeResponse
  filename?: string
  selectedIndex?: number
}

export function TranscriptView({ result, filename, selectedIndex }: TranscriptViewProps) {
  const [copied, setCopied] = useState(false)
  const [showDiff, setShowDiff] = useState(false)
  const queryClient = useQueryClient()
  const { selectedSentence, setSelectedSentence, setResult } = useTranscriptionStore()
  const [reviewWrong, setReviewWrong] = useState<string>('')
  const [reviewRight, setReviewRight] = useState<string>('')
  const [reviewTouched, setReviewTouched] = useState(false)

  const hasSpeakers = result.sentences.some(s => s.speaker_id !== undefined)
  const hasTurns = (result.speaker_turns?.length ?? 0) > 0
  const hasRawText = !!result.raw_text && result.raw_text !== result.text
  const bestPlainText = result.transcript || result.text_accu || result.text

  const selectedSentenceIndex = useMemo(() => {
    if (!selectedSentence) return -1
    return findSentenceIndex(result.sentences, selectedSentence)
  }, [result.sentences, selectedSentence])

  useEffect(() => {
    if (!selectedSentence) {
      setReviewWrong('')
      setReviewRight('')
      setReviewTouched(false)
      return
    }

    const currentText = String(selectedSentence.text || '')
    setReviewWrong(currentText)
    setReviewRight(currentText)
    setReviewTouched(false)
  }, [selectedSentence])

  const reviewDirty = !!selectedSentence && reviewTouched && reviewRight.trim() !== reviewWrong.trim()

  const ruleSuggestion = useMemo(() => {
    if (!reviewDirty) return null
    const wrong = reviewWrong.trim()
    const right = reviewRight.trim()
    if (!wrong || !right) return null

    const diff = extractSimpleDiff(wrong, right)
    const ruleWrong = (diff.wrong || wrong).trim()
    const ruleRight = (diff.right || right).trim()
    if (!ruleWrong || !ruleRight) return null

    return {
      wrong: ruleWrong,
      right: ruleRight,
      preview: `${ruleWrong} = ${ruleRight}`,
    }
  }, [reviewDirty, reviewRight, reviewWrong])

  const applyEditToCurrentResult = () => {
    if (!selectedSentence || selectedSentenceIndex < 0) {
      toast.error('请先在分句/时间轴里选中一句再审校')
      return false
    }

    const nextText = reviewRight.trim()
    if (!nextText) {
      toast.error('正句不能为空')
      return false
    }

    const nextSentences = result.sentences.map((s, idx) => (
      idx === selectedSentenceIndex ? { ...s, text: nextText } : s
    ))

    const nextTurns = buildSpeakerTurns(nextSentences)
    const nextTranscript =
      nextTurns && nextTurns.length > 0
        ? formatTranscript(nextTurns, { includeTimestamp: true })
        : result.transcript
    const nextPlainText = nextSentences.map((s) => String(s.text || '')).join('')

    const nextResult: TranscribeResponse = {
      ...result,
      sentences: nextSentences,
      // Make exports/copy reflect the latest edits immediately.
      text: nextPlainText,
      text_accu: null,
      transcript: nextTranscript,
      speaker_turns: nextTurns,
      // Force SRT export to regenerate from the edited sentences.
      srt: null,
    }

    setResult(nextResult)
    setSelectedSentence(nextResult.sentences[selectedSentenceIndex] || null)
    toast.success('已应用到本次转写结果')
    return true
  }

  const appendRectifyMutation = useMutation({
    mutationFn: async () => {
      const wrong = reviewWrong.trim()
      const right = reviewRight.trim()
      return await appendRectifyRecord(wrong, right)
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles'] })
      toast.success(res.message || '纠错记录已写入', { description: `当前 ${res.count} 条` })
    },
    onError: (e) => {
      toast.error('写入纠错历史失败', { description: e instanceof Error ? e.message : String(e) })
    },
  })

  const appendRuleMutation = useMutation({
    mutationFn: async () => {
      const wrong = reviewWrong.trim()
      const right = reviewRight.trim()
      const suggestion = ruleSuggestion
      const ruleWrong = suggestion?.wrong || wrong
      const ruleRight = suggestion?.right || right

      // Append as a literal-matching regex rule: escape the pattern so it doesn't
      // accidentally act like a regex.
      const pattern = escapeRegExpLiteral(ruleWrong)
      const replacement = escapePythonReplacementLiteral(ruleRight)
      const line = `${pattern} = ${replacement}\n`
      return await appendRulesText(line)
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['hotfiles'] })
      toast.success(res.message || '规则已追加', { description: `当前 ${res.count} 条` })
    },
    onError: (e) => {
      toast.error('写入规则替换失败', { description: e instanceof Error ? e.message : String(e) })
    },
  })

  const handleCopy = async () => {
    await navigator.clipboard.writeText(bestPlainText || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleSelectSentence = (sentence: SentenceInfo) => {
    setSelectedSentence(sentence)
  }

  return (
    <div className="space-y-4">
      {/* 工具栏 */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-4">
          {hasRawText && (
            <div className="flex items-center gap-2">
              <Switch
                id="show-diff"
                checked={showDiff}
                onCheckedChange={setShowDiff}
              />
              <Label htmlFor="show-diff" className="text-sm">
                显示纠错对比
              </Label>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleCopy}>
            {copied ? (
              <>
                <Check className="h-4 w-4 mr-2" />
                已复制
              </>
            ) : (
              <>
                <Copy className="h-4 w-4 mr-2" />
                复制
              </>
            )}
          </Button>
          <ExportMenu result={result} filename={filename} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* 主内容区 */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">转写结果</CardTitle>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue={hasTurns ? 'turns' : 'sentences'}>
              <TabsList className="mb-4">
                <TabsTrigger value="sentences" className="gap-2">
                  <List className="h-4 w-4" />
                  分句视图
                </TabsTrigger>
                {hasTurns && (
                  <TabsTrigger value="turns" className="gap-2">
                    <Users className="h-4 w-4" />
                    说话人段落
                  </TabsTrigger>
                )}
                <TabsTrigger value="full" className="gap-2">
                  <AlignLeft className="h-4 w-4" />
                  全文视图
                </TabsTrigger>
              </TabsList>

              <TabsContent value="sentences">
                <ScrollArea className="h-[400px] pr-4">
                  <SentenceList
                    sentences={result.sentences}
                    rawText={showDiff ? result.raw_text : undefined}
                    showDiff={showDiff}
                    showSpeaker={hasSpeakers}
                    selectedIndex={selectedSentenceIndex >= 0 ? selectedSentenceIndex : selectedIndex}
                    onSelectSentence={(sentence) => handleSelectSentence(sentence)}
                  />
                </ScrollArea>
              </TabsContent>

              {hasTurns && (
                <TabsContent value="turns">
                  <ScrollArea className="h-[400px] pr-4">
                    <div className="space-y-3">
                      {(result.speaker_turns || []).map((t, idx) => (
                        <div key={idx} className="rounded-md border p-3">
                          <div className="flex items-center justify-between gap-2">
                            <SpeakerBadge speaker={t.speaker} speakerId={t.speaker_id} />
                            <span className="text-xs text-muted-foreground tabular-nums">
                              {formatDuration(t.start)} - {formatDuration(t.end)}
                            </span>
                          </div>
                          <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">
                            {t.text}
                          </div>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </TabsContent>
              )}

              <TabsContent value="full">
                <ScrollArea className="h-[400px]">
                  <div className="whitespace-pre-wrap text-sm leading-relaxed p-2">
                    {bestPlainText}
                  </div>
                </ScrollArea>
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>

        {/* 侧边栏 */}
        <div className="space-y-4">
          {/* 说话人统计 */}
          {hasSpeakers && <SpeakerStats sentences={result.sentences} />}

          {/* 审校闭环 */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">审校闭环</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {selectedSentence ? (
                <>
                  <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span className="tabular-nums">
                      {formatClockTime(selectedSentence.start)}–{formatClockTime(selectedSentence.end)}
                    </span>
                    {selectedSentence.speaker || selectedSentence.speaker_id !== undefined ? (
                      <span className="truncate">
                        {selectedSentence.speaker || `说话人${(selectedSentence.speaker_id ?? 0) + 1}`}
                      </span>
                    ) : null}
                  </div>

                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">原句（错句）</Label>
                    <div className="rounded-md border bg-muted/20 p-2 text-sm whitespace-pre-wrap">
                      {reviewWrong || '—'}
                    </div>
                  </div>

                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">正句（可编辑）</Label>
                    <Textarea
                      value={reviewRight}
                      onChange={(e) => {
                        setReviewTouched(true)
                        setReviewRight(e.target.value)
                      }}
                      className="min-h-[84px] text-sm"
                      placeholder="把这一句改成正确文本…"
                    />
                  </div>

                  {ruleSuggestion ? (
                    <div className="rounded-md border bg-muted/20 p-2">
                      <div className="text-xs text-muted-foreground">规则预览（将作为字面量匹配写入 hot-rules.txt）</div>
                      <div className="mt-1 font-mono text-xs whitespace-pre-wrap break-all">
                        {ruleSuggestion.preview}
                      </div>
                    </div>
                  ) : null}

                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => applyEditToCurrentResult()}
                      disabled={!reviewDirty}
                      title="只修改本次转写结果（不写入热词文件）"
                    >
                      应用到本次结果
                    </Button>

                    <Button
                      size="sm"
                      onClick={() => {
                        const applied = applyEditToCurrentResult()
                        if (!applied) return
                        appendRectifyMutation.mutate()
                      }}
                      disabled={!reviewDirty || appendRectifyMutation.isPending || appendRuleMutation.isPending}
                      title="写入 hot-rectify.txt（建议替换：供 LLM 检索使用）"
                    >
                      写入纠错历史
                    </Button>

                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        const applied = applyEditToCurrentResult()
                        if (!applied) return
                        appendRuleMutation.mutate()
                      }}
                      disabled={!reviewDirty || appendRectifyMutation.isPending || appendRuleMutation.isPending}
                      title="写入 hot-rules.txt（强制替换：对所有结果生效，慎用）"
                    >
                      写入规则替换
                    </Button>
                  </div>

                  <p className="text-xs text-muted-foreground">
                    纠错历史：建议替换（配合 LLM 润色更有效）；规则替换：强制替换（适合单位/格式/符号）。
                  </p>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  在「分句视图 / 时间轴」里点选一句，即可编辑并一键写入纠错历史或规则替换。
                </p>
              )}
            </CardContent>
          </Card>

          {/* 基本信息 */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">基本信息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">句子数</span>
                <span className="font-medium">{result.sentences.length}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">字符数</span>
                <span className="font-medium">{result.text.length}</span>
              </div>
              {result.sentences.length > 0 && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">总时长</span>
                  <span className="font-medium">
                    {formatDuration(result.sentences[result.sentences.length - 1].end)}
                  </span>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function formatClockTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const seconds = totalSeconds % 60
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const hours = Math.floor(totalSeconds / 3600)

  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function findSentenceIndex(sentences: SentenceInfo[], selected: SentenceInfo): number {
  const start = Number(selected.start)
  const end = Number(selected.end)
  const speakerId = selected.speaker_id

  // Prefer stable time range match.
  let idx = sentences.findIndex((s) => Number(s.start) === start && Number(s.end) === end)
  if (idx >= 0) return idx

  // Fallback: include speaker_id if available.
  if (speakerId !== undefined) {
    idx = sentences.findIndex(
      (s) => Number(s.start) === start && Number(s.end) === end && s.speaker_id === speakerId
    )
    if (idx >= 0) return idx
  }

  // Last resort: match by text (best-effort).
  const text = String(selected.text || '').trim()
  if (!text) return -1
  return sentences.findIndex((s) => String(s.text || '').trim() === text)
}

function buildSpeakerTurns(sentences: SentenceInfo[], opts?: { gapMs?: number; minChars?: number }): SpeakerTurn[] | null {
  if (!sentences.length) return null

  const hasSpeaker = sentences.some((s) => s.speaker || s.speaker_id !== undefined)
  if (!hasSpeaker) return null

  const gapMs = Math.max(0, Number(opts?.gapMs ?? 800))
  const minChars = Math.max(0, Number(opts?.minChars ?? 1))

  const turns: SpeakerTurn[] = []
  let current: SpeakerTurn | null = null

  const flush = () => {
    if (!current) return
    if (String(current.text || '').trim().length >= minChars) {
      turns.push(current)
    }
    current = null
  }

  for (const s of sentences) {
    const speakerId = typeof s.speaker_id === 'number' ? s.speaker_id : -1
    const speaker = String(s.speaker || (speakerId >= 0 ? `说话人${speakerId + 1}` : '未知'))
    const start = Number(s.start) || 0
    const end = Number(s.end) || start
    const text = String(s.text || '')

    if (!current) {
      current = {
        speaker,
        speaker_id: speakerId,
        start,
        end,
        text,
        sentence_count: 1,
      }
      continue
    }

    const sameSpeaker = current.speaker_id === speakerId && current.speaker === speaker
    const gap = start - (Number(current.end) || 0)
    const canMerge = sameSpeaker && gap <= gapMs

    if (canMerge) {
      current.end = Math.max(Number(current.end) || 0, end)
      current.text = `${current.text || ''}${text}`
      current.sentence_count = Number(current.sentence_count || 0) + 1
    } else {
      flush()
      current = {
        speaker,
        speaker_id: speakerId,
        start,
        end,
        text,
        sentence_count: 1,
      }
    }
  }

  flush()
  return turns
}

function formatTranscript(
  items: Array<{ speaker?: string; speaker_id?: number; start: number; end: number; text: string }>,
  opts?: { includeTimestamp?: boolean }
): string {
  const includeTimestamp = opts?.includeTimestamp ?? true
  return items
    .map((it) => {
      const speakerId = typeof it.speaker_id === 'number' ? it.speaker_id : -1
      const speaker = String(it.speaker || (speakerId >= 0 ? `说话人${speakerId + 1}` : '未知'))
      const text = String(it.text || '')
      if (!includeTimestamp) {
        return `${speaker}: ${text}`
      }
      return `[${formatClockTime(it.start)} - ${formatClockTime(it.end)}] ${speaker}: ${text}`
    })
    .join('\n')
}

function escapeRegExpLiteral(text: string): string {
  // Escape characters with special meaning in Python `re`.
  return String(text).replaceAll(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function escapePythonReplacementLiteral(text: string): string {
  // Python `re.sub` replacement treats backslashes as escapes / group refs.
  // Doubling makes it a literal backslash.
  return String(text).replaceAll('\\', '\\\\')
}

function extractSimpleDiff(wrong: string, right: string): { wrong: string; right: string } {
  const a = String(wrong || '')
  const b = String(right || '')
  if (!a || !b || a === b) return { wrong: '', right: '' }

  let i = 0
  const minLen = Math.min(a.length, b.length)
  while (i < minLen && a[i] === b[i]) i++

  let j = 0
  while (
    j < a.length - i &&
    j < b.length - i &&
    a[a.length - 1 - j] === b[b.length - 1 - j]
  ) {
    j++
  }

  const wrongMid = a.slice(i, a.length - j)
  const rightMid = b.slice(i, b.length - j)
  return { wrong: wrongMid, right: rightMid }
}
