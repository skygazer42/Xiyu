from fastapi import APIRouter
from src.api.routes.transcribe import router as transcribe_router
from src.api.routes.websocket import router as websocket_router
from src.api.routes.hotwords import router as hotwords_router
from src.api.routes.async_transcribe import router as async_router
from src.api.routes.config import router as config_router
from src.api.routes.backend import router as backend_router
from src.api.routes.preprocess import router as preprocess_router

api_router = APIRouter()
api_router.include_router(transcribe_router)
api_router.include_router(websocket_router)
api_router.include_router(hotwords_router)
api_router.include_router(async_router)
api_router.include_router(config_router)
api_router.include_router(backend_router)
api_router.include_router(preprocess_router)

__all__ = ['api_router']
