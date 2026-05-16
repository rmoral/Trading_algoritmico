"""Application settings loaded from environment variables.

All configuration that affects which environment we connect to, where
to find external services, and which credentials to use is loaded here
through `pydantic-settings`. Runtime trading parameters (risk limits,
S/R thresholds, profit range, etc.) live in the database and are
edited through the web app — they are NOT in this module.

Live trading is gated by both a boolean env var and the IBKR port: any
inconsistent combination is rejected at startup.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


IBKR_PAPER_PORT: int = 4002
IBKR_LIVE_PORT: int = 4001


class Settings(BaseSettings):
    """Process-wide settings sourced from environment variables.

    Field names use snake_case; matching env vars use UPPER_SNAKE_CASE
    (the default mapping from `pydantic-settings`). An optional `.env`
    file at the repo root is loaded automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== IBKR connection =====
    ibkr_host: str = Field(default="127.0.0.1", description="IB Gateway host")
    ibkr_port: int = Field(
        default=IBKR_PAPER_PORT,
        description="IB Gateway port: 4002 paper, 4001 live",
    )
    ibkr_client_id: int = Field(default=1, description="IBKR API client id")
    ibkr_account: str = Field(default="", description="IBKR account number (paper has DU prefix)")

    # ===== Live trading safety =====
    live_trading: bool = Field(
        default=False,
        description="Must be True AND ibkr_port=4001 for live orders to be allowed.",
    )
    kill_switch: bool = Field(
        default=False,
        description="If True at startup, the bot trips the circuit breaker immediately.",
    )

    # ===== Database =====
    database_url: str = Field(
        default="postgresql+asyncpg://tradingbot:tradingbot@localhost:5432/tradingbot",
        description="Async SQLAlchemy URL for PostgreSQL.",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ===== Telegram =====
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_chat_id: str = Field(default="")

    # ===== Web API =====
    web_api_host: str = Field(default="127.0.0.1")
    web_api_port: int = Field(default=8000)
    web_api_secret_key: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Used to sign session cookies. Generate with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        ),
    )
    web_admin_username: str = Field(default="admin")
    web_admin_password: SecretStr = Field(default=SecretStr(""))
    web_cookie_secure: bool = Field(
        default=True,
        description=(
            "Send the session cookie only over HTTPS. Override to False ONLY "
            "for local development without TLS. Production always True."
        ),
    )
    web_session_lifetime_days: int = Field(default=30)

    # ===== Logging =====
    log_level: LogLevel = Field(default=LogLevel.INFO)
    log_format: LogFormat = Field(default=LogFormat.JSON)

    # ===== Observability =====
    sentry_dsn: str = Field(default="")

    @field_validator("ibkr_port")
    @classmethod
    def _ibkr_port_is_known(cls, value: int) -> int:
        if value not in {IBKR_PAPER_PORT, IBKR_LIVE_PORT}:
            raise ValueError(
                f"ibkr_port must be {IBKR_PAPER_PORT} (paper) or "
                f"{IBKR_LIVE_PORT} (live); got {value}."
            )
        return value

    @model_validator(mode="after")
    def _live_trading_matches_port(self) -> Self:
        """Belt-and-suspenders for live trading.

        Live order submission is allowed only when BOTH conditions hold:
        - `live_trading=True`, AND
        - `ibkr_port == 4001` (live gateway port).

        Any other combination is rejected at startup. See CLAUDE.md §2
        principle 1 ("Paper trading first, always").
        """
        if self.live_trading and self.ibkr_port != IBKR_LIVE_PORT:
            raise ValueError(
                f"live_trading=True requires ibkr_port={IBKR_LIVE_PORT} (live); "
                f"current port is {self.ibkr_port}."
            )
        if not self.live_trading and self.ibkr_port == IBKR_LIVE_PORT:
            raise ValueError(
                f"ibkr_port={IBKR_LIVE_PORT} (live) requires live_trading=True "
                "to be set explicitly."
            )
        return self

    @property
    def is_live(self) -> bool:
        """True iff this process is configured for live trading."""
        return self.live_trading and self.ibkr_port == IBKR_LIVE_PORT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application settings singleton."""
    return Settings()
