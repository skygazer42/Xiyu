"use client"

import * as React from "react"
import {
  Wifi,
  WifiOff,
  Loader2,
  RotateCcw,
  Signal,
  SignalLow,
  SignalMedium,
  SignalHigh,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "reconnecting"

export interface ConnectionStatusProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 连接状态 */
  status: ConnectionStatus
  /** 延迟 (ms) */
  latency?: number | null
  /** 连接 ID */
  connectionId?: string | null
  /** 是否紧凑模式 */
  compact?: boolean
  /** 重连尝试次数 */
  reconnectAttempt?: number
}

const statusConfig = {
  disconnected: {
    icon: WifiOff,
    label: "未连接",
    color: "text-muted-foreground",
    bgColor: "bg-muted",
    badgeVariant: "secondary" as const,
  },
  connecting: {
    icon: Loader2,
    label: "连接中",
    color: "text-yellow-500",
    bgColor: "bg-yellow-500/10",
    badgeVariant: "outline" as const,
  },
  connected: {
    icon: Wifi,
    label: "已连接",
    color: "text-green-500",
    bgColor: "bg-green-500/10",
    badgeVariant: "outline" as const,
  },
  reconnecting: {
    icon: RotateCcw,
    label: "重连中",
    color: "text-yellow-500",
    bgColor: "bg-yellow-500/10",
    badgeVariant: "outline" as const,
  },
}

function getSignalStrength(latency: number): "low" | "medium" | "high" {
  if (latency > 500) return "low"
  if (latency > 200) return "medium"
  return "high"
}

function ConnectionStatusComponent({
  className,
  status,
  latency,
  connectionId,
  compact = false,
  reconnectAttempt,
  ...props
}: ConnectionStatusProps) {
  const config = statusConfig[status]
  const StatusIcon = config.icon
  const isAnimating = status === "connecting" || status === "reconnecting"

  const signalStrength =
    latency !== null && latency !== undefined ? getSignalStrength(latency) : null
  const signalIconClassName = cn(
    "h-4 w-4",
    signalStrength === "high" && "text-green-500",
    signalStrength === "medium" && "text-yellow-500",
    signalStrength === "low" && "text-red-500"
  )

  if (compact) {
    return (
      <Badge
        variant={config.badgeVariant}
        className={cn("gap-1.5", config.color, className)}
        {...props}
      >
        <StatusIcon
          className={cn("h-3 w-3", isAnimating && "animate-spin")}
        />
        {config.label}
        {latency !== null && latency !== undefined && status === "connected" && (
          <span className="text-xs opacity-70">{latency}ms</span>
        )}
      </Badge>
    )
  }

  return (
    <div
      className={cn(
        "flex items-center gap-3 p-3 rounded-lg",
        config.bgColor,
        className
      )}
      {...props}
    >
      {/* 状态图标 */}
      <div className={cn("p-2 rounded-full bg-background", config.color)}>
        <StatusIcon
          className={cn("h-5 w-5", isAnimating && "animate-spin")}
        />
      </div>

      {/* 状态信息 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={cn("font-medium", config.color)}>
            {config.label}
          </span>
          {status === "reconnecting" && reconnectAttempt && (
            <span className="text-xs text-muted-foreground">
              (尝试 #{reconnectAttempt})
            </span>
          )}
        </div>

        {connectionId && status === "connected" && (
          <p className="text-xs text-muted-foreground truncate">
            ID: {connectionId}
          </p>
        )}
      </div>

      {/* 网络质量 */}
      {latency !== null && latency !== undefined && status === "connected" && (
        <div className="flex items-center gap-2 text-sm">
          {signalStrength === "high" ? (
            <SignalHigh className={signalIconClassName} />
          ) : signalStrength === "medium" ? (
            <SignalMedium className={signalIconClassName} />
          ) : signalStrength === "low" ? (
            <SignalLow className={signalIconClassName} />
          ) : (
            <Signal className={signalIconClassName} />
          )}
          <span className="tabular-nums">{latency}ms</span>
        </div>
      )}
    </div>
  )
}

// LLM 状态指示器
type LLMStatus = "idle" | "generating" | "cancelled"

export interface LLMStatusIndicatorProps extends React.HTMLAttributes<HTMLDivElement> {
  status: LLMStatus
  onCancel?: () => void
}

const llmStatusConfig = {
  idle: {
    label: "LLM 就绪",
    color: "text-muted-foreground",
    show: false,
  },
  generating: {
    label: "润色中...",
    color: "text-primary",
    show: true,
  },
  cancelled: {
    label: "已取消",
    color: "text-yellow-500",
    show: true,
  },
}

function LLMStatusIndicator({
  className,
  status,
  onCancel,
  ...props
}: LLMStatusIndicatorProps) {
  const config = llmStatusConfig[status]

  if (!config.show) return null

  return (
    <div
      className={cn(
        "flex items-center gap-2 px-3 py-1.5 rounded-full bg-muted",
        className
      )}
      {...props}
    >
      {status === "generating" && (
        <Loader2 className="h-3 w-3 animate-spin text-primary" />
      )}
      <span className={cn("text-sm", config.color)}>{config.label}</span>
      {status === "generating" && onCancel && (
        <button
          onClick={onCancel}
          className="text-xs text-muted-foreground hover:text-foreground underline"
        >
          取消
        </button>
      )}
    </div>
  )
}

export { ConnectionStatusComponent as ConnectionStatus, LLMStatusIndicator }
