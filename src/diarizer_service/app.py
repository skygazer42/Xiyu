from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

import src.diarizer_service.routes as diarizer_routes
from src.diarizer_service.schemas import HealthResponse


logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _start_background_warmup() -> None:
    def _warmup() -> None:
        t0 = time.time()
        try:
            logger.info("Diarizer warmup started (model download/load)")
            diarizer_routes.engine.load()
            logger.info(f"Diarizer warmup finished in {time.time() - t0:.2f}s")
        except Exception as e:
            logger.warning(f"Diarizer warmup failed (ignored): {e}")

    threading.Thread(target=_warmup, name="diarizer-warmup", daemon=True).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if _env_bool("DIARIZER_WARMUP_ON_STARTUP", default=False):
        _start_background_warmup()
    yield


app = FastAPI(title="Xiyu Diarizer Service", version="1.0.0", lifespan=lifespan)

app.include_router(diarizer_routes.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="healthy")


if __name__ == "__main__":
    import argparse

    import uvicorn

    default_host = os.getenv("DIARIZER_HOST", "0.0.0.0")
    default_port = int(os.getenv("DIARIZER_PORT") or os.getenv("PORT") or "8300")

    parser = argparse.ArgumentParser(description="Run Xiyu Diarizer Service (FastAPI)")
    parser.add_argument("--host", default=default_host, help="Bind host (default: DIARIZER_HOST or 0.0.0.0)")
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help="Bind port (default: DIARIZER_PORT or PORT or 8300)",
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only)")
    args = parser.parse_args()

    uvicorn.run(
        "src.diarizer_service.app:app",
        host=str(args.host),
        port=int(args.port),
        reload=bool(args.reload),
    )
