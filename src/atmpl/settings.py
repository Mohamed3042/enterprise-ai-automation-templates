"""One typed settings object; every deployment knob is an ``ATMPL_`` environment variable."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SQLITE_PATH = Path("var") / "atmpl.db"


def _default_database_url() -> str:
    return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


class Settings(BaseSettings):
    """Runtime configuration. Defaults are the keyless, offline, SQLite demo."""

    model_config = SettingsConfigDict(
        env_prefix="ATMPL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- persistence -------------------------------------------------------
    database_url: str = Field(default_factory=_default_database_url)
    auto_create_schema: bool | None = None

    # --- model access ------------------------------------------------------
    adapter: str = Field(
        default="mock",
        validation_alias=AliasChoices("ATMPL_ADAPTER", "ATMPL_LLM_ADAPTER"),
    )

    # --- secrets -----------------------------------------------------------
    secrets_backend: str = "env"
    secrets_file: Path | None = None

    # --- HTTP hardening ----------------------------------------------------
    allowed_origins: list[str] = Field(default_factory=list)
    rate_limit: str = "120/minute"
    max_body_bytes: int = 1_048_576
    hsts_enabled: bool = False
    csp_script_src: list[str] = Field(default_factory=lambda: ["https://unpkg.com"])

    # --- identity ----------------------------------------------------------
    admin_user: str | None = None
    admin_password_hash: str | None = None
    session_ttl_seconds: int = 43_200
    token_ttl_seconds: int = 900
    jwt_key_id: str = "atmpl-hs256-1"

    # --- demo boundary -----------------------------------------------------
    demo_open_api: bool = False

    # --- webhooks ----------------------------------------------------------
    webhook_worker: bool = False
    webhook_worker_interval_seconds: float = 2.0
    webhook_max_attempts: int = 5
    webhook_timeout_seconds: float = 10.0
    webhook_tolerance_seconds: int = 300
    public_base_url: str = "http://127.0.0.1:8000"

    @field_validator("allowed_origins", "csp_script_src", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("secrets_backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        if value not in {"env", "file"}:
            raise ValueError(f"Unknown secrets backend '{value}'. Allowed: env, file")
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def create_schema_on_start(self) -> bool:
        """SQLite demos create their schema in-process; servers run migrations first."""
        if self.auto_create_schema is not None:
            return self.auto_create_schema
        return self.is_sqlite

    @property
    def demo_mode(self) -> bool:
        """No admin credentials configured -> the dashboard is an open demo."""
        return not (self.admin_user and self.admin_password_hash)

    def rate_limit_parts(self) -> tuple[int, float]:
        """``"120/minute"`` -> ``(120, 60.0)``. ``"0/minute"`` disables the limiter."""
        windows = {"second": 1.0, "minute": 60.0, "hour": 3600.0, "day": 86_400.0}
        raw = self.rate_limit.strip().lower()
        count, _, window = raw.partition("/")
        try:
            capacity = int(count)
        except ValueError as exc:
            raise ValueError(f"Invalid ATMPL_RATE_LIMIT '{self.rate_limit}'") from exc
        window = window.strip() or "minute"
        if window not in windows:
            raise ValueError(
                f"Invalid ATMPL_RATE_LIMIT window '{window}'. Allowed: {', '.join(windows)}"
            )
        return capacity, windows[window]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def settings_from_env(**overrides: object) -> Settings:
    """Build a fresh Settings, optionally skipping the on-disk .env (used by tests)."""
    env_file = None if os.getenv("ATMPL_IGNORE_ENV_FILE") else ".env"
    return Settings(_env_file=env_file, **overrides)  # type: ignore[arg-type]
