"""服务指标收集模块

提供简单的内存指标收集，支持 Prometheus 格式导出。
"""
import time
import threading
from typing import Dict, Any
from dataclasses import dataclass, field


@dataclass
class ServiceMetrics:
    """服务指标收集器"""

    # 启动时间
    start_time: float = field(default_factory=time.time)

    # 请求计数
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # 音频处理统计
    total_audio_seconds: float = 0.0
    total_processing_seconds: float = 0.0

    # WebSocket 连接
    active_ws_connections: int = 0
    total_ws_connections: int = 0

    # External diarizer (speaker diarization) stats
    diarizer_requests_total: int = 0
    diarizer_failures_total: int = 0
    diarizer_latency_seconds_sum: float = 0.0
    diarizer_latency_seconds_count: int = 0

    # 线程锁
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def increment_requests(self) -> None:
        """增加请求计数"""
        with self._lock:
            self.total_requests += 1

    def increment_success(self) -> None:
        """增加成功计数"""
        with self._lock:
            self.successful_requests += 1

    def increment_failure(self) -> None:
        """增加失败计数"""
        with self._lock:
            self.failed_requests += 1

    def add_audio_duration(self, duration: float) -> None:
        """添加音频时长"""
        with self._lock:
            self.total_audio_seconds += duration

    def add_processing_time(self, duration: float) -> None:
        """添加处理时间"""
        with self._lock:
            self.total_processing_seconds += duration

    def ws_connect(self) -> None:
        """WebSocket 连接"""
        with self._lock:
            self.active_ws_connections += 1
            self.total_ws_connections += 1

    def ws_disconnect(self) -> None:
        """WebSocket 断开"""
        with self._lock:
            self.active_ws_connections = max(0, self.active_ws_connections - 1)

    def record_diarizer_call(self, *, success: bool, latency_s: float) -> None:
        """Record an external diarizer call outcome + latency (best-effort)."""
        try:
            latency = float(latency_s)
        except (TypeError, ValueError):
            latency = 0.0
        if latency < 0:
            latency = 0.0

        with self._lock:
            self.diarizer_requests_total += 1
            if not success:
                self.diarizer_failures_total += 1
            self.diarizer_latency_seconds_sum += latency
            self.diarizer_latency_seconds_count += 1

    @property
    def uptime_seconds(self) -> float:
        """服务运行时间"""
        return time.time() - self.start_time

    @property
    def avg_rtf(self) -> float:
        """平均实时因子 (Real-Time Factor)"""
        if self.total_audio_seconds == 0:
            return 0.0
        return self.total_processing_seconds / self.total_audio_seconds

    def get_stats(self) -> Dict[str, Any]:
        """获取所有指标"""
        with self._lock:
            return {
                "uptime_seconds": self.uptime_seconds,
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "total_audio_seconds": self.total_audio_seconds,
                "total_processing_seconds": self.total_processing_seconds,
                "avg_rtf": self.avg_rtf,
                "active_ws_connections": self.active_ws_connections,
                "total_ws_connections": self.total_ws_connections,
                "diarizer_requests_total": self.diarizer_requests_total,
                "diarizer_failures_total": self.diarizer_failures_total,
                "diarizer_latency_seconds_sum": self.diarizer_latency_seconds_sum,
                "diarizer_latency_seconds_count": self.diarizer_latency_seconds_count,
            }

    def to_prometheus(self) -> str:
        """导出为 Prometheus 格式"""
        stats = self.get_stats()
        lines = [
            "# HELP xiyu_uptime_seconds Service uptime in seconds",
            "# TYPE xiyu_uptime_seconds gauge",
            f"xiyu_uptime_seconds {stats['uptime_seconds']:.2f}",
            "",
            "# HELP xiyu_requests_total Total number of requests",
            "# TYPE xiyu_requests_total counter",
            f"xiyu_requests_total {stats['total_requests']}",
            "",
            "# HELP xiyu_requests_successful_total Successful requests",
            "# TYPE xiyu_requests_successful_total counter",
            f"xiyu_requests_successful_total {stats['successful_requests']}",
            "",
            "# HELP xiyu_requests_failed_total Failed requests",
            "# TYPE xiyu_requests_failed_total counter",
            f"xiyu_requests_failed_total {stats['failed_requests']}",
            "",
            "# HELP xiyu_audio_seconds_total Total audio processed in seconds",
            "# TYPE xiyu_audio_seconds_total counter",
            f"xiyu_audio_seconds_total {stats['total_audio_seconds']:.2f}",
            "",
            "# HELP xiyu_processing_seconds_total Total processing time in seconds",
            "# TYPE xiyu_processing_seconds_total counter",
            f"xiyu_processing_seconds_total {stats['total_processing_seconds']:.2f}",
            "",
            "# HELP xiyu_rtf_avg Average Real-Time Factor",
            "# TYPE xiyu_rtf_avg gauge",
            f"xiyu_rtf_avg {stats['avg_rtf']:.4f}",
            "",
            "# HELP xiyu_websocket_connections_active Active WebSocket connections",
            "# TYPE xiyu_websocket_connections_active gauge",
            f"xiyu_websocket_connections_active {stats['active_ws_connections']}",
            "",
            "# HELP xiyu_websocket_connections_total Total WebSocket connections",
            "# TYPE xiyu_websocket_connections_total counter",
            f"xiyu_websocket_connections_total {stats['total_ws_connections']}",
            "",
            "# HELP xiyu_diarizer_requests_total Total external diarizer calls",
            "# TYPE xiyu_diarizer_requests_total counter",
            f"xiyu_diarizer_requests_total {stats['diarizer_requests_total']}",
            "",
            "# HELP xiyu_diarizer_failures_total Failed external diarizer calls",
            "# TYPE xiyu_diarizer_failures_total counter",
            f"xiyu_diarizer_failures_total {stats['diarizer_failures_total']}",
            "",
            "# HELP xiyu_diarizer_latency_seconds External diarizer latency summary",
            "# TYPE xiyu_diarizer_latency_seconds summary",
            f"xiyu_diarizer_latency_seconds_sum {stats['diarizer_latency_seconds_sum']:.4f}",
            f"xiyu_diarizer_latency_seconds_count {stats['diarizer_latency_seconds_count']}",
        ]
        return "\n".join(lines)

    def reset(self) -> None:
        """重置指标 (保留启动时间)"""
        with self._lock:
            self.total_requests = 0
            self.successful_requests = 0
            self.failed_requests = 0
            self.total_audio_seconds = 0.0
            self.total_processing_seconds = 0.0
            self.total_ws_connections = 0
            self.diarizer_requests_total = 0
            self.diarizer_failures_total = 0
            self.diarizer_latency_seconds_sum = 0.0
            self.diarizer_latency_seconds_count = 0


# 全局指标实例
metrics = ServiceMetrics()
