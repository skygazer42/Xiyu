"use client"

import * as React from "react"
import {
  Search,
  Trash2,
  Eye,
  Clock,
  FileAudio,
  ChevronDown,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { EmptyStateNoData } from "@/components/ui/empty-state"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { ExportMenu } from "@/components/transcript"
import { useHistoryStore, type HistoryItem } from "@/stores/historyStore"

export interface HistoryListProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 查看结果回调 */
  onViewResult?: (item: HistoryItem) => void
}

function HistoryList({
  className,
  onViewResult,
  ...props
}: HistoryListProps) {
  const {
    items,
    searchQuery,
    isLoaded,
    load,
    removeItem,
    clearAll,
    setSearchQuery,
    getFilteredItems,
    getStorageSize,
  } = useHistoryStore()

  const [expandedId, setExpandedId] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (!isLoaded) {
      load()
    }
  }, [isLoaded, load])

  const filteredItems = getFilteredItems()
  const storageSize = getStorageSize()

  const formatDate = (timestamp: number): string => {
    const date = new Date(timestamp)
    const now = new Date()
    const isToday = date.toDateString() === now.toDateString()

    if (isToday) {
      return `今天 ${date.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })}`
    }

    const yesterday = new Date(now)
    yesterday.setDate(yesterday.getDate() - 1)
    const isYesterday = date.toDateString() === yesterday.toDateString()

    if (isYesterday) {
      return `昨天 ${date.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })}`
    }

    return date.toLocaleString("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  }

  return (
    <Card className={className} {...props}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">
            转写历史 ({items.length})
          </CardTitle>
          {items.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {storageSize.toFixed(1)} MB
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={clearAll}
                className="text-destructive hover:text-destructive"
              >
                清空全部
              </Button>
            </div>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* 搜索 */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索历史..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>

        {/* 历史列表 */}
        {filteredItems.length === 0 ? (
          <EmptyStateNoData
            title={searchQuery ? "未找到匹配的历史" : "暂无转写历史"}
            description={
              searchQuery ? "尝试调整搜索关键词" : "转写完成后将自动保存到历史记录"
            }
            size="sm"
          />
        ) : (
          <div className="space-y-2 max-h-[500px] overflow-y-auto">
            {filteredItems.map((item) => (
              <Collapsible
                key={item.id}
                open={expandedId === item.id}
                onOpenChange={(open) => setExpandedId(open ? item.id : null)}
              >
                {(() => {
                  const hasSpeaker =
                    (item.speakerTurns?.length ?? 0) > 0 ||
                    (item.sentences || []).some((s) => s.speaker_id !== undefined || !!s.speaker)
                  const baseName = item.filename.replace(/\.[^/.]+$/, "")
                  const exportResult = {
                    code: 0,
                    text: item.text,
                    text_accu: item.textAccu ?? null,
                    sentences: item.sentences,
                    speaker_turns: item.speakerTurns ?? null,
                    transcript: item.transcript,
                    srt: item.srt ?? null,
                    raw_text: item.rawText,
                  }
                  const preview = String(item.transcript || item.textAccu || item.text || "")
                    .replace(/\s+/g, " ")
                    .trim()

                  return (
                <div className="rounded-lg border bg-card overflow-hidden">
                  <div className="flex items-start gap-3 p-3">
                    <FileAudio className="h-5 w-5 text-muted-foreground mt-0.5 shrink-0" />

                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm truncate">
                        {item.filename}
                      </p>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {formatDate(item.timestamp)}
                        </span>
                        {item.options?.applyLlm && (
                          <Badge variant="secondary" className="text-xs h-4">
                            LLM
                          </Badge>
                        )}
                        {hasSpeaker && (
                          <Badge variant="outline" className="text-xs h-4">
                            说话人
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                        {preview.slice(0, 120)}
                        {preview.length > 120 ? "..." : ""}
                      </p>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      <CollapsibleTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-8 w-8">
                          <ChevronDown
                            className={cn(
                              "h-4 w-4 transition-transform",
                              expandedId === item.id && "rotate-180"
                            )}
                          />
                        </Button>
                      </CollapsibleTrigger>
                    </div>
                  </div>

                  <CollapsibleContent>
                    <div className="border-t px-3 py-3 bg-muted/30 space-y-3">
                      <div className="text-sm whitespace-pre-wrap max-h-[200px] overflow-y-auto">
                        {item.transcript || item.textAccu || item.text}
                      </div>
                      <div className="flex items-center gap-2">
                        {onViewResult && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onViewResult(item)}
                          >
                            <Eye className="h-4 w-4 mr-1" />
                            查看
                          </Button>
                        )}
                        <ExportMenu result={exportResult} filename={`${baseName}-转写`} />
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive ml-auto"
                          onClick={() => removeItem(item.id)}
                        >
                          <Trash2 className="h-4 w-4 mr-1" />
                          删除
                        </Button>
                      </div>
                    </div>
                  </CollapsibleContent>
                </div>
                  )
                })()}
              </Collapsible>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export { HistoryList }
