"use client"

import * as React from "react"
import { ExternalLink, Copy, Download, Code, RefreshCw, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { toast } from "@/lib/toast"
import { useBackendStore } from "@/stores"

export interface PrometheusPanelProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Prometheus 端点 URL */
  endpoint?: string
  /** 原始指标数据 */
  metricsData?: string | null
  /** 是否加载中 */
  isLoading?: boolean
}

function buildPrometheusConfig(target: string) {
  return `scrape_configs:
  - job_name: 'xiyu'
    scrape_interval: 15s
    static_configs:
      - targets: ['${target}']
    metrics_path: '/metrics/prometheus'`
}

const GRAFANA_DASHBOARD = {
  dashboard: {
    title: "Xiyu ASR Monitor",
    panels: [
      {
        title: "Total Requests",
        type: "stat",
        targets: [{ expr: 'xiyu_requests_total' }],
      },
      {
        title: "Success Rate",
        type: "gauge",
        targets: [{ expr: 'xiyu_requests_successful_total / xiyu_requests_total * 100' }],
      },
      {
        title: "Average RTF",
        type: "timeseries",
        targets: [{ expr: 'xiyu_rtf_avg' }],
      },
    ],
  },
}

function PrometheusPanel({
  className,
  endpoint = '/metrics/prometheus',
  metricsData,
  isLoading = false,
  ...props
}: PrometheusPanelProps) {
  const { baseUrl } = useBackendStore()
  const [previewText, setPreviewText] = React.useState<string | null>(null)
  const [isPreviewLoading, setIsPreviewLoading] = React.useState(false)
  const [previewError, setPreviewError] = React.useState<string | null>(null)

  const prometheusTarget = React.useMemo(() => {
    if (!baseUrl) {
      return window.location.host
    }
    try {
      return new URL(baseUrl).host
    } catch {
      return window.location.host
    }
  }, [baseUrl])

  const prometheusConfig = React.useMemo(() => {
    return buildPrometheusConfig(prometheusTarget)
  }, [prometheusTarget])

  const fullEndpoint = React.useMemo(() => {
    const path = endpoint.startsWith("/") ? endpoint : `/${endpoint}`
    if (!baseUrl) {
      return `${window.location.origin}${path}`
    }
    return `${baseUrl}${path}`
  }, [baseUrl, endpoint])

  const handleCopyEndpoint = () => {
    navigator.clipboard.writeText(fullEndpoint)
    toast.success('端点链接已复制')
  }

  const handleCopyConfig = () => {
    navigator.clipboard.writeText(prometheusConfig)
    toast.success('Prometheus 配置已复制')
  }

  const handleDownloadDashboard = () => {
    const json = JSON.stringify(GRAFANA_DASHBOARD, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'xiyu-grafana-dashboard.json'
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Grafana Dashboard 已下载')
  }

  const handleOpenEndpoint = () => {
    window.open(fullEndpoint, '_blank')
  }

  const handleFetchPreview = async () => {
    setIsPreviewLoading(true)
    setPreviewError(null)
    try {
      const res = await fetch(fullEndpoint, { cache: "no-store" })
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`)
      }
      const text = await res.text()
      setPreviewText(text)
      toast.success("指标已拉取")
    } catch (e) {
      const msg = e instanceof Error ? e.message : "请求失败"
      setPreviewError(msg)
      toast.error("指标拉取失败", { description: msg })
    } finally {
      setIsPreviewLoading(false)
    }
  }

  const effectiveMetricsData = metricsData ?? previewText
  const effectiveLoading = isLoading || isPreviewLoading

  return (
    <Card className={className} {...props}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">Prometheus 集成</CardTitle>
            <CardDescription>监控指标导出和集成配置</CardDescription>
          </div>
          <Badge variant="outline">OpenMetrics</Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* 端点信息 */}
        <div className="space-y-2">
          <label className="text-sm font-medium">端点地址</label>
          <div className="flex items-center gap-2 p-3 rounded-lg bg-muted font-mono text-sm">
            <code className="flex-1 truncate">{fullEndpoint}</code>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={handleCopyEndpoint}
            >
              <Copy className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={handleOpenEndpoint}
            >
              <ExternalLink className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Prometheus 配置 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">Prometheus 配置</label>
            <Button variant="ghost" size="sm" onClick={handleCopyConfig}>
              <Copy className="h-4 w-4 mr-1" />
              复制
            </Button>
          </div>
          <pre className="p-3 rounded-lg bg-muted text-xs font-mono overflow-x-auto">
            {prometheusConfig}
          </pre>
        </div>

        {/* Grafana Dashboard */}
        <div className="flex items-center justify-between p-3 rounded-lg border">
          <div className="flex items-center gap-3">
            <Code className="h-5 w-5 text-muted-foreground" />
            <div>
              <p className="text-sm font-medium">Grafana Dashboard</p>
              <p className="text-xs text-muted-foreground">
                预配置的仪表盘模板
              </p>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={handleDownloadDashboard}>
            <Download className="h-4 w-4 mr-1" />
            下载
          </Button>
        </div>

        {/* 指标预览 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">指标预览</label>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleFetchPreview}
              disabled={effectiveLoading}
            >
              {effectiveLoading ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4 mr-1" />
              )}
              拉取
            </Button>
          </div>

          {previewError && (
            <p className="text-xs text-destructive">
              {previewError}
            </p>
          )}

          {effectiveMetricsData && (
            <pre
              className={cn(
                "p-3 rounded-lg bg-muted text-xs font-mono overflow-auto max-h-[200px]",
                effectiveLoading && "animate-pulse"
              )}
            >
              {effectiveMetricsData}
            </pre>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export { PrometheusPanel }
