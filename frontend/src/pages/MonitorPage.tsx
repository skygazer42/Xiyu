import { useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Loader2, RefreshCw, Activity, Clock, CheckCircle, Zap, Database } from 'lucide-react'
import { checkHealth, getMetrics } from '@/lib/api'
import { ResponsiveContainer, PieChart, Pie, Cell } from 'recharts'
import { PrometheusPanel } from '@/components/monitor/PrometheusPanel'
import { useBackendStore } from '@/stores'

export default function MonitorPage() {
  const { baseUrl } = useBackendStore()

  // 健康检查
  const healthQuery = useQuery({
    queryKey: ['health', baseUrl],
    queryFn: checkHealth,
    refetchInterval: 30000, // 30 秒刷新
  })

  // 指标
  const metricsQuery = useQuery({
    queryKey: ['metrics', baseUrl],
    queryFn: getMetrics,
    refetchInterval: 10000, // 10 秒刷新
  })

  const isHealthy = healthQuery.data?.status === 'healthy'
  const metrics = metricsQuery.data

  // 格式化运行时间
  const formatUptime = (seconds: number) => {
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)

    if (days > 0) return `${days}天 ${hours}小时`
    if (hours > 0) return `${hours}小时 ${minutes}分钟`
    return `${minutes}分钟`
  }

  // 计算成功率
  const successRate =
    metrics && metrics.total_requests > 0
      ? (metrics.successful_requests / metrics.total_requests) * 100
      : null

  // 饼图数据
  const pieData = metrics
    ? [
        { name: '成功', value: metrics.successful_requests, color: '#22c55e' },
        { name: '失败', value: metrics.failed_requests, color: '#ef4444' },
      ]
    : []

  // LLM 缓存统计
  const cacheStats = metrics?.llm_cache_stats || {}

  const handleRefresh = () => {
    healthQuery.refetch()
    metricsQuery.refetch()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">系统监控</h1>
          <p className="text-muted-foreground">查看服务运行状态和性能指标</p>
        </div>
        <Button
          variant="outline"
          onClick={handleRefresh}
          disabled={healthQuery.isLoading || metricsQuery.isLoading}
        >
          <RefreshCw
            className={`h-4 w-4 mr-2 ${
              healthQuery.isLoading || metricsQuery.isLoading ? 'animate-spin' : ''
            }`}
          />
          刷新
        </Button>
      </div>

      {/* 健康状态 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            服务状态
            {healthQuery.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Badge
                variant="outline"
                className={
                  isHealthy
                    ? 'bg-green-500/10 text-green-600 border-green-200'
                    : 'bg-red-500/10 text-red-600 border-red-200'
                }
              >
                {isHealthy ? '在线' : '离线'}
              </Badge>
            )}
          </CardTitle>
          {healthQuery.data && (
            <CardDescription>版本: {healthQuery.data.version}</CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              icon={Clock}
              label="运行时间"
              value={metrics ? formatUptime(metrics.uptime_seconds) : '-'}
              loading={metricsQuery.isLoading}
            />
            <MetricCard
              icon={Activity}
              label="总请求数"
              value={metrics?.total_requests.toLocaleString() ?? '-'}
              loading={metricsQuery.isLoading}
            />
            <MetricCard
              icon={CheckCircle}
              label="成功率"
              value={successRate === null ? '-' : `${successRate.toFixed(1)}%`}
              loading={metricsQuery.isLoading}
              valueClassName={
                successRate === null
                  ? undefined
                  : successRate >= 95
                  ? 'text-green-600'
                  : successRate >= 80
                    ? 'text-yellow-600'
                    : 'text-red-600'
              }
            />
            <MetricCard
              icon={Zap}
              label="平均 RTF"
              value={metrics && metrics.avg_rtf > 0 ? metrics.avg_rtf.toFixed(3) : '-'}
              loading={metricsQuery.isLoading}
              description="实时因子 (越小越快)"
            />
          </div>
        </CardContent>
      </Card>

      {/* 图表区域 */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* 请求统计饼图 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5" />
              请求统计
            </CardTitle>
            <CardDescription>成功与失败请求占比</CardDescription>
          </CardHeader>
          <CardContent>
            {metrics && metrics.total_requests > 0 ? (
              <div className="h-[200px] flex items-center justify-center">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={pieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={50}
                      outerRadius={80}
                      paddingAngle={2}
                      dataKey="value"
                    >
                      {pieData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex flex-col gap-2 ml-4">
                  <div className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-full bg-green-500" />
                    <span className="text-sm">
                      成功: {metrics.successful_requests.toLocaleString()}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500" />
                    <span className="text-sm">
                      失败: {metrics.failed_requests.toLocaleString()}
                    </span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="h-[200px] flex items-center justify-center text-muted-foreground">
                暂无请求数据
              </div>
            )}
          </CardContent>
        </Card>

        {/* 音频处理统计 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5" />
              处理统计
            </CardTitle>
            <CardDescription>音频处理时长和 LLM 缓存</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
                <span className="text-muted-foreground">处理音频总时长</span>
                <span className="font-medium">
                  {metrics
                    ? formatAudioDuration(metrics.total_audio_seconds)
                    : '-'}
                </span>
              </div>

              {Object.keys(cacheStats).length > 0 && (
                <>
                  <div className="text-sm font-medium">LLM 缓存统计</div>
                  <div className="space-y-2">
                    {Object.entries(cacheStats).map(([key, value]) => (
                      <div
                        key={key}
                        className="flex items-center justify-between p-2 rounded bg-muted/30"
                      >
                        <span className="text-sm text-muted-foreground">
                          {formatCacheKey(key)}
                        </span>
                        <span className="text-sm font-mono">
                          {String(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {Object.keys(cacheStats).length === 0 && (
                <div className="flex items-center justify-center h-20 text-muted-foreground text-sm">
                  LLM 缓存未启用或无数据
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 额外信息 */}
      <Card>
        <CardHeader>
          <CardTitle>实时指标</CardTitle>
          <CardDescription>最近的性能数据</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">成功请求</p>
              <p className="text-2xl font-bold text-green-600">
                {metrics?.successful_requests.toLocaleString() ?? '-'}
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">失败请求</p>
              <p className="text-2xl font-bold text-red-600">
                {metrics?.failed_requests.toLocaleString() ?? '-'}
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">平均处理速度</p>
              <p className="text-2xl font-bold">
                {metrics && metrics.avg_rtf > 0 ? `${(1 / metrics.avg_rtf).toFixed(1)}x` : '-'}
              </p>
              <p className="text-xs text-muted-foreground">相对实时速度</p>
            </div>
          </div>
        </CardContent>
      </Card>
      {/* Prometheus 集成 */}
      <PrometheusPanel />
    </div>
  )
}

interface MetricCardProps {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
  loading?: boolean
  description?: string
  valueClassName?: string
}

function MetricCard({
  icon: Icon,
  label,
  value,
  loading,
  description,
  valueClassName,
}: MetricCardProps) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="h-4 w-4" />
        <p className="text-sm">{label}</p>
      </div>
      {loading ? (
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      ) : (
        <p className={`text-2xl font-bold ${valueClassName ?? ''}`}>{value}</p>
      )}
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  )
}

function formatAudioDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)

  if (hours > 0) {
    return `${hours}小时 ${minutes}分钟`
  }
  if (minutes > 0) {
    return `${minutes}分钟 ${secs}秒`
  }
  return `${secs}秒`
}

function formatCacheKey(key: string): string {
  const keyMap: Record<string, string> = {
    hits: '命中次数',
    misses: '未命中次数',
    hit_rate: '命中率',
    size: '缓存大小',
    max_size: '最大容量',
  }
  return keyMap[key] || key
}
