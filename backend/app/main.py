"""FastAPI application factory. All routes live under /api; nginx proxies them."""

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_fastapi_instrumentator import Instrumentator

from app.auth.router import router as auth_router
from app.config import get_settings
from app.errors import install_error_handlers
from app.logging import configure_logging, get_logger
from app.routers.cabinets import router as cabinets_router
from app.routers.grammar import router as grammar_router
from app.routers.me import router as me_router
from app.routers.reference import router as reference_router
from app.routers.system import router as system_router

API_PREFIX = "/api"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.check_external_ai()
    get_logger(__name__).info(
        "startup",
        env=settings.app_env,
        demo_mode=settings.demo_mode,
        external_ai=settings.allow_external_ai,
    )
    yield
    from app.db import engine

    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Тренажёр оператора ДДС-112",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if settings.app_env != "production" else None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        response = await call_next(request)
        if not request.url.path.endswith(("/health", "/metrics")):
            get_logger("http").info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=round((time.perf_counter() - started) * 1000, 1),
            )
        response.headers["X-Request-ID"] = request_id
        return response

    Instrumentator(
        should_group_status_codes=False,
        excluded_handlers=[f"{API_PREFIX}/metrics", f"{API_PREFIX}/health"],
    ).instrument(app).expose(app, endpoint=f"{API_PREFIX}/metrics", include_in_schema=False)

    app.include_router(system_router, prefix=API_PREFIX)
    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(me_router, prefix=API_PREFIX)
    app.include_router(cabinets_router, prefix=API_PREFIX)
    app.include_router(reference_router, prefix=API_PREFIX)
    app.include_router(grammar_router, prefix=API_PREFIX)
    return app


app = create_app()
