"""FastAPI application factory. All routes live under /api; nginx proxies them."""

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_fastapi_instrumentator import Instrumentator

from app import warmup
from app.admin import health as health_monitor
from app.admin import settings as admin_settings
from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.config import get_settings
from app.dialog.router import router as dialog_router
from app.errors import install_error_handlers
from app.intake.router import router as intake_router
from app.logging import configure_logging, get_logger
from app.routers.cabinets import router as cabinets_router
from app.routers.grammar import router as grammar_router
from app.routers.me import router as me_router
from app.routers.reference import router as reference_router
from app.routers.system import router as system_router
from app.scenarios.router import router as scenarios_router
from app.telephony import service as telephony
from app.telephony.router import router as telephony_router
from app.training import sweeper
from app.training.review import router as review_router
from app.training.router import router as training_router
from app.training.teacher import router as teacher_router
from app.training.ws import router as ws_router

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
    # The «Не оповещено» sweep needs a running loop; tests call it explicitly instead.
    sweep_task = sweeper.start() if settings.app_env != "test" else None
    monitor_task = None
    if settings.app_env != "test":
        from app.db import SessionLocal

        async with SessionLocal() as db:
            await admin_settings.apply_stored(db)
        await telephony.start()
        warmup.start()
        monitor_task = health_monitor.start()
    yield
    await health_monitor.stop(monitor_task)
    await telephony.stop()
    if sweep_task is not None:
        await sweeper.stop(sweep_task)
    from app.db import engine
    from app.events import close_redis

    await close_redis()
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
    app.include_router(training_router, prefix=API_PREFIX)
    app.include_router(scenarios_router, prefix=API_PREFIX)
    app.include_router(teacher_router, prefix=API_PREFIX)
    app.include_router(dialog_router, prefix=API_PREFIX)
    app.include_router(telephony_router, prefix=API_PREFIX)
    app.include_router(intake_router, prefix=API_PREFIX)
    app.include_router(admin_router, prefix=API_PREFIX)
    app.include_router(review_router, prefix=API_PREFIX)
    # WebSocket lives outside /api: nginx proxies /ws/ with the upgrade headers.
    app.include_router(ws_router)
    return app


app = create_app()
