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

    # Local AI services (compose profile `ai`). Empty address = the fallback without a model.
    # llama.cpp servers speak the OpenAI-compatible chat API; STT is our own small server with
    # the OpenAI-compatible transcription endpoint (deploy/stt).
    llm_dialog_url: str | None = None
    llm_gen_url: str | None = None
    stt_url: str | None = None
    # How the caller answers (PRD 9.3): select | hybrid | generate | buttons | live.
    # Without a reachable dialog model every mode degrades to `buttons`.
    dialog_mode: Literal["select", "hybrid", "generate", "buttons", "live"] = "select"
    # Folder with downloaded models (scripts/fetch_models.sh): tts/ (Piper voices), stt/, llm/.
    models_dir: str | None = None
    # Writable folder for generated files (voiced replies, recordings); /storage in compose.
    storage_dir: str = "../storage"

    # Telephony (PRD 9.5, compose profile `telephony`). Off: the call panel of the browser
    # works through the microphone and /attempts/{id}/utterance instead of a SIP call.
    telephony_enabled: bool = False
    # ARI of Asterisk: HTTP base (the WebSocket of events is derived from it) and the user
    # from deploy/asterisk/ari.conf.
    ari_url: str = "http://asterisk:8088/ari"
    ari_user: str = "trainer"
    ari_password: str = "trainer"  # noqa: S105 - development default, see .env.example
    ari_app: str = "trainer"
    # Where Asterisk sends the operator's audio (ExternalMedia): this host as Asterisk sees it
    # (empty = the container's own hostname) and the UDP ports used, one per call.
    telephony_media_host: str = ""
    telephony_media_port_start: int = 12000
    telephony_media_port_end: int = 12100
    # Folder with the generated PJSIP endpoints, shared with the Asterisk container
    # (empty = do not write; the endpoints then come from pjsip.conf only).
    asterisk_config_dir: str | None = None
    # The recordings folder as Asterisk sees it (mounted from STORAGE_DIR/recordings) and
    # the voiced replies folder for playback (mounted from STORAGE_DIR/tts).
    asterisk_recording_dir: str = "/var/spool/asterisk/recording"
    asterisk_sounds_dir: str = "/var/lib/asterisk/sounds/trainer"
    # Path of the SIP WebSocket in the browser (nginx proxies it to Asterisk).
    sip_ws_path: str = "/ws/sip"
    sip_domain: str = "trainer"
    # Seconds the softphone rings before the call is given up.
    call_ring_timeout_seconds: int = 45
    # Silero VAD model (ONNX); missing file = energy-based detector.
    vad_model_path: str | None = None

    @field_validator("database_url", "database_admin_url")
    @classmethod
    def _must_be_asyncpg(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("ожидается адрес вида postgresql+asyncpg://…")
        return value

    def ai_service_urls(self) -> dict[str, str]:
        """Configured AI endpoints by their .env name (only the non-empty ones)."""
        urls = {
            "LLM_DIALOG_URL": self.llm_dialog_url,
            "LLM_GEN_URL": self.llm_gen_url,
            "STT_URL": self.stt_url,
        }
        return {name: url for name, url in urls.items() if url}

    def external_ai_hosts(self) -> dict[str, str]:
        """AI endpoints that point outside the local contour, by .env name."""
        external = {}
        for name, url in self.ai_service_urls().items():
            host = urlparse(url).hostname or ""
            if not _is_local_host(host):
                external[name] = host
        return external

    def check_external_ai(self) -> None:
        """PRD section 2: refuse to start with an external model when ALLOW_EXTERNAL_AI=false."""
        if self.allow_external_ai:
            return
        external = self.external_ai_hosts()
        if external:
            listed = ", ".join(f"{name} → {host}" for name, host in external.items())
            raise RuntimeError(
                f"Внешний адрес ИИ-сервиса при ALLOW_EXTERNAL_AI=false: {listed}. "
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
