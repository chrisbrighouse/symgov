from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .routes.admin import legacy_router as legacy_admin_router
from .routes.admin import router as admin_router
from .routes.public import legacy_router as legacy_public_router
from .routes.public import router as public_router
from .routes.published import legacy_router as legacy_published_router
from .routes.published import router as published_router
from .routes.workspace import legacy_router as legacy_workspace_router
from .routes.workspace import router as workspace_router
from .agent_queue_worker import AgentQueueWorkerConfig, run_agent_queue_worker
from .settings import get_settings


def load_app_version() -> str:
    package_json_path = Path(__file__).resolve().parents[2] / "package.json"
    return json.loads(package_json_path.read_text(encoding="utf-8"))["version"]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Symgov API", version=load_app_version())

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        error = "not_found" if exc.status_code == 404 else "request_error"
        return JSONResponse(status_code=exc.status_code, content={"error": error, "detail": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "detail": "Request validation failed.", "issues": exc.errors()},
        )

    app.include_router(admin_router, prefix=settings.api_prefix)
    app.include_router(public_router, prefix=settings.api_prefix)
    app.include_router(published_router, prefix=settings.api_prefix)
    app.include_router(workspace_router, prefix=settings.api_prefix)
    app.include_router(legacy_admin_router, prefix="/api")
    app.include_router(legacy_public_router, prefix="/api")
    app.include_router(legacy_published_router, prefix="/api")
    app.include_router(legacy_workspace_router, prefix="/api")

    @app.on_event("startup")
    async def start_background_workers() -> None:
        if not settings.enable_agent_workers and not settings.enable_libby_worker:
            return
        agents = settings.agent_workers if settings.enable_agent_workers else ("libby",)
        stop_event = asyncio.Event()
        config = AgentQueueWorkerConfig(
            agents=agents,
            db_env_file=settings.db_env_file,
            storage_env_file=settings.storage_env_file,
            interval_seconds=settings.agent_worker_interval_seconds
            if settings.enable_agent_workers
            else settings.libby_worker_interval_seconds,
            limit=settings.agent_worker_limit if settings.enable_agent_workers else settings.libby_worker_limit,
            drain=settings.agent_worker_drain,
        )
        app.state.agent_worker_stop_event = stop_event
        app.state.agent_worker_task = asyncio.create_task(run_agent_queue_worker(config, stop_event))

    @app.on_event("shutdown")
    async def stop_background_workers() -> None:
        task = getattr(app.state, "agent_worker_task", None)
        stop_event = getattr(app.state, "agent_worker_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return app


app = create_app()
