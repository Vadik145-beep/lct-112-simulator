"""ARQ background worker. Real jobs (generation, TTS, reports, backups) come in later waves."""

from typing import ClassVar

from arq.connections import RedisSettings

from app.config import get_settings
from app.logging import configure_logging, get_logger


async def ping(ctx: dict) -> str:
    """Smoke job used by the healthcheck and tests."""
    get_logger(__name__).info("ping", job_id=ctx.get("job_id"))
    return "pong"


async def startup(ctx: dict) -> None:
    configure_logging(get_settings().log_level)
    get_logger(__name__).info("worker started")


class WorkerSettings:
    functions: ClassVar = [ping]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    health_check_interval = 30
    max_jobs = 10
