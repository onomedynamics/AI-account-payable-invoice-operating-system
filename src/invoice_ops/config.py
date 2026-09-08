"""Typed application settings, loaded once from the environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database -------------------------------------------------------------
    # Dev default is a local SQLite file (no service needed). Point at Postgres
    # in CI / prod via DATABASE_URL.
    database_url: str = "sqlite:///./dev.db"
    sql_echo: bool = False

    # --- Task queue --------------------------------------------------------
    # Eager mode runs tasks inline in the calling process, so no broker is
    # needed for local dev. Set CELERY_TASK_ALWAYS_EAGER=false plus a real
    # broker URL for prod-shaped runs.
    celery_task_always_eager: bool = True
    celery_broker_url: str = "memory://"
    celery_result_backend: str = "cache+memory://"

    # --- Object storage --------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: Path = Path("./_storage")
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "invoice-ops"

    # --- Uploads --------------------------------------------------------
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MiB
    allowed_upload_content_types: tuple[str, ...] = (
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
    )

    # --- App ---------------------------------------------------------------
    app_env: Literal["dev", "ci", "prod"] = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
