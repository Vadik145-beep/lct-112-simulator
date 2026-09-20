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
    # Analytics folder (PRD 9.7): models/ holds the readiness forecast model and its metrics,
    # data/ the simulated cohort it was trained on; /ai in compose, ../ai from backend/.
    analytics_dir: str = "../ai"

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

    # Cloud voice (plan/track-c-vapi.md, compose profile `cloud`): in a lesson with
    # dialog_mode=cloud the caller is played by Vapi — over a SIP trunk of the `asterisk-cloud`
    # container when telephony is on, straight from the browser (Vapi Web SDK) when it is off.
    # Demo only, outside the closed contour: requires ALLOW_EXTERNAL_AI=true.
    cloud_voice_enabled: bool = False
    vapi_api_key: str | None = None
    # Public key of the Vapi account: the browser starts web calls with it (no telephony).
    vapi_public_key: str | None = None
    vapi_api_url: str = "https://api.vapi.ai"
    # SIP host of Vapi (sip.vapi.ai or sip.eu.vapi.ai); the trunk in asterisk-cloud points here.
    vapi_sip_host: str = "sip.vapi.ai"
    # Name of the SIP number in the Vapi account; created on start when missing.
    vapi_number_name: str = "dds-trainer"
    # This stand as Vapi reaches it for webhooks: https with a certificate a public CA signed.
    cloud_voice_public_url: str | None = None
    # Secret Vapi sends back in the X-Trainer-Secret header (empty = derived from SECRET_KEY).
    cloud_voice_webhook_secret: str | None = None
    cloud_voice_model_provider: str = "openai"
    cloud_voice_model: str = "gpt-4.1"
    cloud_voice_voice_provider: str = "11labs"
    # The default voice and, when set, the voices by the scenario's caller (ru_male_*,
    # ru_female_*, ru_child_*; the «slow» variants ru_male_3 / ru_female_2 are the elderly).
    cloud_voice_voice_id: str = "3EuKHIEZbSzrHGNmdYsx"
    cloud_voice_voice_id_male: str | None = None
    cloud_voice_voice_id_female: str | None = None
    cloud_voice_voice_id_elder_male: str | None = None
    cloud_voice_voice_id_elder_female: str | None = None
    cloud_voice_voice_id_young: str | None = None
    cloud_voice_voice_model: str = "eleven_multilingual_v2"
    cloud_voice_transcriber_provider: str = "deepgram"
    cloud_voice_transcriber_model: str = "nova-2"
    cloud_voice_language: str = "ru"
    # Longest cloud call in seconds (Vapi ends it) and seconds of silence before it hangs up.
    cloud_voice_max_seconds: int = 900
    cloud_voice_silence_seconds: int = 60
    # Seconds the SIP leg to Vapi may ring before the call falls back to the local pipeline.
    cloud_voice_answer_seconds: int = 15

    # Backups (PRD 14): the folder shared with the `backup` service (/backups in compose), the
    # daily time and how many dumps to keep; the administrator overrides the last two.
    backup_dir: str = "../backups"
    backup_time: str = "03:00"
    backup_keep: int = 14
    # Seconds between two checks of the services by the administrator's monitor
    # (app.admin.health); 0 disables the loop (tests call it explicitly).
    health_monitor_seconds: int = 60

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
        if self.cloud_voice_enabled:
            external["CLOUD_VOICE_ENABLED"] = urlparse(self.vapi_api_url).hostname or "vapi"
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
