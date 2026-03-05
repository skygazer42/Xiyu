"""Xiyu Speech Service 主入口"""
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.api import api_router
from src.api.schemas import HealthResponse, MetricsResponse
from src.utils.service_metrics import metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info(f"Starting {settings.app_name} v{settings.version}")

    # 加载所有热词相关文件
    from src.core.engine import transcription_engine
    transcription_engine.load_all()

    # 模型预热
    if settings.warmup_on_startup:
        try:
            warmup_result = transcription_engine.warmup(
                duration=settings.warmup_audio_duration
            )
            logger.info(f"Warmup result: {warmup_result}")
        except Exception as e:
            logger.warning(f"Warmup failed: {e}")

    # 启动热词文件监视器
    if settings.hotword_watch_enable:
        from src.core.hotword.watcher import setup_hotword_watcher, stop_hotword_watcher
        setup_hotword_watcher(
            watch_dir=str(settings.hotwords_dir),
            on_hotwords_change=lambda path: transcription_engine.load_hotwords(path),
            on_context_hotwords_change=lambda path: transcription_engine.load_context_hotwords(path),
            on_rules_change=lambda path: transcription_engine.load_rules(path),
            on_rectify_change=lambda path: transcription_engine.load_rectify_history(path),
            debounce_delay=settings.hotword_watch_debounce,
            hotwords_filename=settings.hotwords_file,
            context_hotwords_filename=settings.hotwords_context_file,
            rules_filename="hot-rules.txt",
            rectify_filename="hot-rectify.txt",
        )

    # 启动异步任务管理器
    from src.core.task_manager import task_manager
    task_manager.start()

    logger.info("Service ready!")

    yield

    # 停止任务管理器
    task_manager.stop()
    if settings.hotword_watch_enable:
        from src.core.hotword.watcher import stop_hotword_watcher
        stop_hotword_watcher()
    logger.info("Shutting down...")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="基于 FunASR + CapsWriter 的中文语音转写服务",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(api_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check():
    """健康检查"""
    return HealthResponse(status="healthy", version=settings.version)


@app.get("/", tags=["system"])
async def root(request: Request):
    """Root: JSON by default; serve Web UI only for HTML clients.

    Rationale:
    - API clients (including tests/health probes) expect JSON.
    - Browsers typically send `Accept: text/html` and should get the SPA UI when built.
    """
    accept = str(request.headers.get("accept", "") or "").lower()
    wants_html = "text/html" in accept

    if wants_html and FRONTEND_DIST.exists():
        index_file = FRONTEND_DIST / "index.html"
        if index_file.exists() and index_file.is_file():
            return FileResponse(index_file)

    return {
        "name": settings.app_name,
        "version": settings.version,
        "docs": "/docs",
    }


@app.get("/service-info", tags=["system"])
async def service_info():
    """服务信息（JSON）。"""
    return {
        "name": settings.app_name,
        "version": settings.version,
        "docs": "/docs",
    }


@app.get("/metrics", tags=["system"])
async def get_metrics():
    """获取服务指标 (JSON 格式)"""
    stats = metrics.get_stats()

    # 尝试获取 LLM 缓存统计
    llm_cache_stats = {}
    try:
        from src.core.engine import transcription_engine
        if transcription_engine._llm_client:
            llm_cache_stats = transcription_engine._llm_client.get_cache_stats()
    except Exception:
        pass

    return MetricsResponse(
        uptime_seconds=stats["uptime_seconds"],
        total_requests=stats["total_requests"],
        successful_requests=stats["successful_requests"],
        failed_requests=stats["failed_requests"],
        total_audio_seconds=stats["total_audio_seconds"],
        avg_rtf=stats["avg_rtf"],
        llm_cache_stats=llm_cache_stats,
    )


@app.get("/metrics/prometheus", tags=["system"], response_class=PlainTextResponse)
async def get_metrics_prometheus():
    """获取服务指标 (Prometheus 格式)"""
    return metrics.to_prometheus()


# 生产环境：挂载前端静态文件
# 前端构建后放在 frontend/dist 目录
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    # SPA fallback: 所有未匹配的路由返回 index.html
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """SPA 路由回退"""
        # Never mask missing API routes with the SPA shell: this makes debugging
        # deployment/version mismatches much harder (curl would get HTML).
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")

    # 静态资源
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
    logger.info(f"Frontend static files mounted from {FRONTEND_DIST}")


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run Xiyu Speech Service (FastAPI)")
    parser.add_argument("--host", default=str(settings.host), help="Bind host (default: settings.host)")
    parser.add_argument("--port", type=int, default=int(settings.port), help="Bind port (default: settings.port)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only)")
    args = parser.parse_args()

    uvicorn.run(
        "src.main:app",
        host=str(args.host),
        port=int(args.port),
        reload=bool(args.reload),
    )
