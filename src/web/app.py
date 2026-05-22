from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .api import router as api_router
from .state import get_state
from .ws import router as ws_router

_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Jellyfin Media Manager",
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    @app.get("/", include_in_schema=False)
    async def serve_spa() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.on_event("startup")
    async def on_startup() -> None:
        get_state().register_loop(asyncio.get_running_loop())

    return app


app = create_app()
