"""Health, public configuration."""

import asyncio

from fastapi import APIRouter, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.auth.service import passwordless_logins, shown_credentials
from app.config import get_settings
from app.db import engine
from app.models import Role

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    postgres: str
    redis: str
    version: str


class DemoCredential(BaseModel):
    role: Role
    login: str
    password: str


class PublicConfig(BaseModel):
    demo_mode: bool
    # Roles with a «Войти как …» button on the login page (DEMO_MODE or QUICK_LOGIN).
    quick_login_roles: list[Role] = []
    # SHOW_DEMO_CREDENTIALS: login and password by role for the login page.
    demo_credentials: list[DemoCredential] = []
    external_ai: bool
    app_env: str
    dialog_mode: str


async def _check_postgres() -> str:
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=3)
        return "ok"
    except Exception as exc:  # any failure means "down"
        return f"error: {type(exc).__name__}"


async def _check_redis() -> str:
    client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=3)
    try:
        await asyncio.wait_for(client.ping(), timeout=3)
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"
    finally:
        await client.aclose()


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    pg, rd = await asyncio.gather(_check_postgres(), _check_redis())
    ok = pg == "ok" and rd == "ok"
    if not ok:
        response.status_code = 503
    return HealthResponse(status="ok" if ok else "degraded", postgres=pg, redis=rd, version="0.1.0")


@router.get("/config", response_model=PublicConfig)
async def public_config() -> PublicConfig:
    s = get_settings()
    return PublicConfig(
        demo_mode=s.demo_mode,
        quick_login_roles=list(passwordless_logins()),
        demo_credentials=[
            DemoCredential(role=role, login=login, password=s.seed_password)
            for role, login in shown_credentials()
        ],
        external_ai=s.allow_external_ai,
        app_env=s.app_env,
        dialog_mode=s.dialog_mode,
    )
