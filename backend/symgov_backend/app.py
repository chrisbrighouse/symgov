from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .routes.admin import legacy_router as legacy_admin_router
from .routes.admin import router as admin_router
from .routes.public import legacy_router as legacy_public_router
from .routes.public import router as public_router
from .settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Symgov API", version="0.1.0")

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
    app.include_router(legacy_admin_router, prefix="/api")
    app.include_router(legacy_public_router, prefix="/api")
    return app


app = create_app()
