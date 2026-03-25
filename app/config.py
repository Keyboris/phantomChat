"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    session_ttl_minutes: int = 30
    max_session_participants: int = Field(default=2, ge=2, le=10)
    max_payload_bytes: int = 65536
    rate_limit_per_minute: int = 10
    log_level: str = "WARNING"
    allowed_origin: str  # required — no default, must be set in env

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
