import { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import type { EnsembleCandidate, EnsembleTranscribeResponse } from '@/lib/api/types'
import { Check, ChevronDown, Copy, X } from 'lucide-react'

interface EnsemblePanelProps {
  ensemble: EnsembleTranscribeResponse
}

function formatElapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function pickCandidateDisplayText(c: EnsembleCandidate): { title: string; text: string } | null {
  const cleaned = (c.cleaned_text || '').trim()
  const raw = (c.text || '').trim()

  if (cleaned && raw && cleaned !== raw) {
    return { title: 'cleaned_text', text: cleaned }
  }
  if (raw) {
    return { title: 'text', text: raw }
  }
  if (cleaned) {
    return { title: 'cleaned_text', text: cleaned }
  }
  return null
}

export function EnsemblePanel({ ensemble }: EnsemblePanelProps) {
  const [open, setOpen] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [expandedCandidates, setExpandedCandidates] = useState<Record<string, boolean>>({})

  const stats = useMemo(() => {
    const ok = ensemble.candidates.filter((c) => c.success).length
    const total = ensemble.candidates.length
    const fail = total - ok
    return { ok, fail, total }
  }, [ensemble.candidates])

  const copyText = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedKey(key)
      window.setTimeout(() => setCopiedKey((prev) => (prev === key ? null : prev)), 1500)
    } catch {
      // Clipboard can fail (permission / insecure context). Ignore silently.
    }
  }

  return (
    <Card>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle className="text-base">多模型对比</CardTitle>
              <CardDescription>
                全量优化会并发跑多个后端，把候选结果交给大模型参考，输出最终听记稿
              </CardDescription>
            </div>

            <CollapsibleTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                {open ? '收起' : '展开'}
                <ChevronDown className={cn('h-4 w-4 transition-transform', open ? 'rotate-180' : '')} />
              </Button>
            </CollapsibleTrigger>
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Badge variant="outline">base: {ensemble.base_backend}</Badge>
            <Badge variant="outline">role: {ensemble.llm_role}</Badge>
            <Badge
              variant="outline"
              className={ensemble.llm_used ? 'border-green-200 text-green-700 bg-green-500/5' : 'border-amber-200 text-amber-700 bg-amber-500/5'}
            >
              {ensemble.llm_used ? 'LLM 已使用' : 'LLM 未使用'}
            </Badge>
            <Badge variant="outline">
              candidates: {stats.ok}/{stats.total} ok
            </Badge>
            {stats.fail > 0 && (
              <Badge variant="outline" className="border-red-200 text-red-700 bg-red-500/5">
                {stats.fail} failed
              </Badge>
            )}
          </div>
        </CardHeader>

        <CollapsibleContent>
          <CardContent className="space-y-3">
            <div className="grid gap-3">
              {ensemble.candidates.map((c) => {
                const key = `${c.backend}:${c.base_url}`
                const preview = pickCandidateDisplayText(c)
                const expanded = !!expandedCandidates[key]
                const oneLinePreview = preview?.text
                  ? preview.text.replace(/\s+/g, ' ').trim().slice(0, 180) + (preview.text.length > 180 ? '...' : '')
                  : ''
                const cleaned = (c.cleaned_text || '').trim()
                const raw = (c.text || '').trim()
                const sections = [
                  ...(cleaned ? [{ title: 'cleaned_text', text: cleaned, copyKey: `${key}:cleaned` }] : []),
                  ...(raw && raw !== cleaned ? [{ title: 'text', text: raw, copyKey: `${key}:text` }] : []),
                ]

                return (
                  <div key={key} className="rounded-md border p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{c.backend}</span>
                          {c.success ? (
                            <Badge className="bg-green-600">OK</Badge>
                          ) : (
                            <Badge variant="destructive">FAIL</Badge>
                          )}
                          <Badge variant="outline">耗时 {formatElapsed(c.elapsed_ms ?? null)}</Badge>
                          {typeof c.http_status === 'number' && (
                            <Badge variant="outline">HTTP {c.http_status}</Badge>
                          )}
                          {typeof c.code === 'number' && <Badge variant="outline">code {c.code}</Badge>}
                        </div>
                        {!c.success && c.error && (
                          <div className="mt-2 text-xs text-red-700">
                            {c.error}
                          </div>
                        )}
                        {c.success && oneLinePreview && !expanded && (
                          <div className="mt-2 text-xs text-muted-foreground break-words">
                            {oneLinePreview}
                          </div>
                        )}
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        {preview?.text ? (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-2"
                            onClick={() => void copyText(key, preview.text)}
                            title={`复制 ${preview.title}`}
                          >
                            {copiedKey === key ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                            复制
                          </Button>
                        ) : null}
                        {preview?.text ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="gap-2"
                            onClick={() =>
                              setExpandedCandidates((prev) => ({
                                ...prev,
                                [key]: !prev[key],
                              }))
                            }
                          >
                            {expanded ? '收起' : '展开'}
                            <ChevronDown className={cn('h-4 w-4 transition-transform', expanded ? 'rotate-180' : '')} />
                          </Button>
                        ) : null}
                      </div>
                    </div>

                    {preview?.text && expanded ? (
                      <div className="mt-3">
                        <div className="grid gap-3">
                          {sections.map((s) => (
                            <div key={s.copyKey}>
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-xs text-muted-foreground">
                                  {s.title} (最多展示 4000 字符)
                                </span>
                                <div className="flex items-center gap-2">
                                  <Badge variant="outline" className="text-xs">
                                    {s.text.length} chars
                                  </Badge>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="gap-2"
                                    onClick={() => void copyText(s.copyKey, s.text)}
                                    title={`复制 ${s.title}`}
                                  >
                                    {copiedKey === s.copyKey ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                                    复制
                                  </Button>
                                </div>
                              </div>
                              <ScrollArea className="h-[180px] mt-2 rounded-md border bg-muted/30">
                                <pre className="whitespace-pre-wrap text-xs leading-relaxed p-3">
                                  {s.text}
                                </pre>
                              </ScrollArea>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="mt-3 text-xs text-muted-foreground">
                        (无候选文本)
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {!ensemble.llm_used && (
              <div className="rounded-md border border-amber-200 bg-amber-500/5 p-3 text-sm text-amber-800 flex gap-2">
                <X className="h-4 w-4 mt-0.5 shrink-0" />
                <div>
                  <div className="font-medium">本次没有使用 LLM 做融合润色</div>
                  <div className="text-xs mt-1 text-amber-800/80">
                    常见原因：LLM 配置未启用、API Key 不可用、网络问题或 LLM 输出 JSON 不可解析。
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  )
}
