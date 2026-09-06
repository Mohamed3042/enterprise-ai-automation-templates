"""One typed settings object; every deployment knob is an ``ATMPL_`` environment variable."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: A comma-separated list, given as one environment variable.
#:
#: `NoDecode` is load-bearing. pydantic-settings treats any `list[str]` field as complex and
#: runs `json.loads` on the raw value *before* a `mode="before"` validator ever sees it, so
#: `ATMPL_PROVIDER_FALLBACKS=""` — which is what Compose and a Kubernetes ConfigMap pass for
#: "no fallbacks" — raises `SettingsError` and the container never starts. Measured: the kind
#: smoke job in CI caught exactly that, on exactly that variable.
CsvList = Annotated[list[str], NoDecode]

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
    #: Fallback chain tried in order after ``adapter`` fails. Never chosen by a caller (G7).
    provider_fallbacks: CsvList = Field(default_factory=list)
    provider_timeout_seconds: float = 30.0
    provider_max_attempts: int = 3
    provider_backoff_seconds: float = 0.5
    provider_breaker_threshold: int = 3
    provider_breaker_cooldown_seconds: float = 30.0
    gemini_model: str = "gemini-3.6-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    anthropic_model: str = "claude-sonnet-5"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # --- telemetry ---------------------------------------------------------
    #: ``none`` keeps spans in-process (the LLMOps page still works), ``console`` prints
    #: them, ``otlp`` ships them to ``otlp_endpoint``.
    trace_exporter: str = "none"
    otlp_endpoint: str = "http://localhost:4318/v1/traces"
    service_name: str = "atmpl"
    #: How many finished spans and model calls this process keeps for the LLMOps page.
    telemetry_buffer_size: int = 2_000
    metrics_enabled: bool = True
    #: ``/metrics`` without a credential. Turn off to require the ``metrics:read`` scope.
    metrics_public: bool = True
    json_logs: bool = False

    # --- secrets -----------------------------------------------------------
    secrets_backend: str = "env"
    secrets_file: Path | None = None

    # --- HTTP hardening ----------------------------------------------------
    allowed_origins: CsvList = Field(default_factory=list)
    rate_limit: str = "120/minute"
    max_body_bytes: int = 1_048_576
    hsts_enabled: bool = False
    csp_script_src: CsvList = Field(default_factory=lambda: ["https://unpkg.com"])

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

    # --- demo boundary (hosted) -------------------------------------------
    #: The Hugging Face Space sets this: the dashboard renders read-only and every
    #: mutation is refused, so a public demo cannot spend a key or move a run.
    demo_readonly: bool = False

    @field_validator("allowed_origins", "csp_script_src", "provider_fallbacks", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """``"a, b"`` -> ``["a", "b"]``; ``""`` -> ``[]`` rather than a start-up crash."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("trace_exporter")
    @classmethod
    def _known_exporter(cls, value: str) -> str:
        if value not in {"none", "console", "otlp"}:
            raise ValueError(
                f"Unknown ATMPL_TRACE_EXPORTER '{value}'. Allowed: none, console, otlp"
            )
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
    def provider_chain(self) -> list[str]:
        """Primary provider first, then each configured fallback, de-duplicated in order."""
        chain: list[str] = []
        for name in [self.adapter, *self.provider_fallbacks]:
            if name and name not in chain:
                chain.append(name)
        return chain

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
