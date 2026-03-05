"use client"

import * as React from "react"
import {
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
  Eye,
  Trash2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { EmptyStateNoData } from "@/components/ui/empty-state"
import type { TranscribeResponse } from "@/lib/api/types"

export interface Task {
  id: string
  status: "pending" | "processing" | "success" | "error"
  url?: string
  filename?: string
  createdAt: Date
  result?: TranscribeResponse
  error?: string
  progress?: number
  detail?: string
}

export interface TaskManagerProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 任务列表 */
  tasks: Task[]
  /** 查看任务结果 */
  onViewResult?: (task: Task) => void
  /** 删除任务 */
  onRemove?: (taskId: string) => void
  /** 重试任务 */
  onRetry?: (task: Task) => void
  /** 刷新任务状态 */
  onRefresh?: (taskId: string) => void
  /** 是否正在刷新 */
  isRefreshing?: boolean
}

const statusConfig = {
  pending: {
    icon: Clock,
    label: "等待中",
    color: "bg-gray-500",
    badgeVariant: "secondary" as const,
  },
  processing: {
    icon: Loader2,
    label: "处理中",
    color: "bg-yellow-500",
    badgeVariant: "default" as const,
  },
  success: {
    icon: CheckCircle2,
    label: "完成",
    color: "bg-green-500",
    badgeVariant: "default" as const,
  },
  error: {
    icon: XCircle,
    label: "失败",
    color: "bg-red-500",
    badgeVariant: "destructive" as const,
  },
}

function TaskManager({
  className,
  tasks,
  onViewResult,
  onRemove,
  onRetry,
  onRefresh,
  isRefreshing = false,
  ...props
}: TaskManagerProps) {
  const [nowMs, setNowMs] = React.useState(0)

  const processingCount = tasks.filter((t) => t.status === "processing").length
  const successCount = tasks.filter((t) => t.status === "success").length
  const errorCount = tasks.filter((t) => t.status === "error").length

  React.useEffect(() => {
    // Keep render pure; update time via state.
    setNowMs(Date.now())
    const id = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const formatTime = (date: Date): string => {
    return date.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
  }

  const getElapsedTime = (date: Date): string => {
    if (!nowMs) return ""
    const seconds = Math.floor((nowMs - date.getTime()) / 1000)
    if (seconds < 60) return `${seconds}s`
    const minutes = Math.floor(seconds / 60)
    if (minutes < 60) return `${minutes}m`
    const hours = Math.floor(minutes / 60)
    return `${hours}h ${minutes % 60}m`
  }

  if (tasks.length === 0) {
    return (
      <Card className={className} {...props}>
        <CardContent className="pt-6">
          <EmptyStateNoData
            title="暂无任务"
            description="提交 URL 转写后，任务将显示在这里"
            size="sm"
          />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={className} {...props}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">任务队列 ({tasks.length})</CardTitle>
          <div className="flex items-center gap-2 text-xs">
            {processingCount > 0 && (
              <Badge variant="outline" className="gap-1">
                <Loader2 className="h-3 w-3 animate-spin" />
                {processingCount}
              </Badge>
            )}
            {successCount > 0 && (
              <Badge variant="outline" className="gap-1 text-green-600">
                <CheckCircle2 className="h-3 w-3" />
                {successCount}
              </Badge>
            )}
            {errorCount > 0 && (
              <Badge variant="outline" className="gap-1 text-red-600">
                <XCircle className="h-3 w-3" />
                {errorCount}
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-2">
        {tasks.map((task) => {
          const config = statusConfig[task.status]
          const StatusIcon = config.icon

          return (
            <div
              key={task.id}
              className="flex items-center gap-3 p-3 rounded-lg bg-muted/50"
            >
              {/* 状态图标 */}
              <div
                className={cn(
                  "p-1.5 rounded-full text-white shrink-0",
                  config.color
                )}
              >
                <StatusIcon
                  className={cn(
                    "h-4 w-4",
                    task.status === "processing" && "animate-spin"
                  )}
                />
              </div>

              {/* 任务信息 */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">
                  {task.filename || task.id}
                </p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{formatTime(task.createdAt)}</span>
                  {(task.status === "pending" || task.status === "processing") && (
                    <span>{getElapsedTime(task.createdAt) ? `(${getElapsedTime(task.createdAt)})` : null}</span>
                  )}
                  {task.progress !== undefined && task.status === "processing" && (
                    <span className="text-primary">{task.progress}%</span>
                  )}
                </div>
                {task.error && (
                  <p className="text-xs text-red-500 mt-0.5 truncate">
                    {task.error}
                  </p>
                )}
                {!task.error && task.detail && (task.status === "pending" || task.status === "processing") && (
                  <p className="text-xs text-muted-foreground mt-0.5 truncate">{task.detail}</p>
                )}
              </div>

              {/* 操作按钮 */}
              <div className="flex items-center gap-1 shrink-0">
                {task.status === "success" && onViewResult && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => onViewResult(task)}
                  >
                    <Eye className="h-4 w-4" />
                  </Button>
                )}

                {task.status === "error" && onRetry && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => onRetry(task)}
                  >
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                )}

                {(task.status === "pending" || task.status === "processing") &&
                  onRefresh && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => onRefresh(task.id)}
                      disabled={isRefreshing}
                    >
                      <RefreshCw
                        className={cn(
                          "h-4 w-4",
                          isRefreshing && "animate-spin"
                        )}
                      />
                    </Button>
                  )}

                {onRemove && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-muted-foreground hover:text-destructive"
                    onClick={() => onRemove(task.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

export { TaskManager }
