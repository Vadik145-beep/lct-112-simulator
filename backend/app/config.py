"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env is looked up in the repository root and next to the backend; real env vars win.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["development", "production", "test"] = "development"
    demo_mode: bool = True
    allow_external_ai: bool = False
    tz: str = "Europe/Moscow"
    log_level: str = "INFO"

    secret_key: str = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    cookie_secure: bool = True

    database_url: str
    database_admin_url: str
    app_db_user: str = "trainer_app"
    redis_url: str = "redis://redis:6379/0"

    seed_password: str = "Demo12345"  # noqa: S105 - demo default, see .env.example
    languagetool_url: str = "http://languagetool:8010"
    # Dataset folder: data/organizers (source files, not committed) and data/seed (derived,
    # committed). Mounted read-only into the containers; ../data when running from backend/.
    data_dir: str = "../data"
    # Local sentence-transformers model for embeddings; TF-IDF is used when it is absent.
    embedding_model_dir: str | None = None

    # Optional OpenAI-compatible endpoint for local models (used from wave 5).
    llm_base_url: str | None = None

    @field_validator("database_url", "database_admin_url")
    @classmethod
    def _must_be_asyncpg(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("ожидается адрес вида postgresql+asyncpg://…")
        return value

    def check_external_ai(self) -> None:
        """PRD section 2: refuse to start with an external model when ALLOW_EXTERNAL_AI=false."""
        if self.allow_external_ai or not self.llm_base_url:
            return
        host = urlparse(self.llm_base_url).hostname or ""
        if not _is_local_host(host):
            raise RuntimeError(
                f"LLM_BASE_URL указывает на внешний адрес {host}, а ALLOW_EXTERNAL_AI=false. "
                "В закрытом контуре внешние ИИ запрещены: укажите локальный сервер модели "
                "или включите ALLOW_EXTERNAL_AI=true только для разработки."
            )


_LOCAL_SUFFIXES = (".local", ".internal", ".lan")


def _is_local_host(host: str) -> bool:
    """Hostnames that resolve inside the compose network or private ranges."""
    if host in {"localhost", "127.0.0.1", "::1"} or "." not in host:
        return True
    if host.endswith(_LOCAL_SUFFIXES):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
    return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
