"""Xiyu API package.

Keep imports lightweight so utility modules under `src.api.*` (e.g. `asr_options`)
can be imported without requiring FastAPI route dependencies at import time.
"""

from __future__ import annotations

from typing import Any

__all__ = ["api_router"]


def __getattr__(name: str) -> Any:
    if name == "api_router":
        from src.api.routes import api_router

        return api_router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
