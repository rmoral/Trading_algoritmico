"""Unit tests for `tradingbot.settings`.

These verify the safety invariants around live trading:
- only known IBKR ports (4001 / 4002) are accepted,
- `live_trading=True` requires the live port,
- the live port requires `live_trading=True`.

No external services are needed; tests run without a network or DB.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tradingbot.settings import (
    IBKR_LIVE_PORT,
    IBKR_PAPER_PORT,
    LogFormat,
    LogLevel,
    Settings,
)


def _make(**overrides: Any) -> Settings:
    """Build Settings without loading any `.env` from disk."""
    base: dict[str, Any] = {
        "ibkr_host": "127.0.0.1",
        "ibkr_port": IBKR_PAPER_PORT,
        "ibkr_client_id": 1,
        "live_trading": False,
    }
    base.update(overrides)
    # `_env_file=None` disables loading the on-disk .env during tests.
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def test_defaults_are_paper_safe() -> None:
    settings = _make()
    assert settings.ibkr_port == IBKR_PAPER_PORT
    assert settings.live_trading is False
    assert settings.is_live is False


def test_unknown_port_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _make(ibkr_port=4444)


def test_live_trading_requires_live_port() -> None:
    with pytest.raises(ValidationError):
        _make(live_trading=True, ibkr_port=IBKR_PAPER_PORT)


def test_live_port_requires_live_trading_flag() -> None:
    with pytest.raises(ValidationError):
        _make(live_trading=False, ibkr_port=IBKR_LIVE_PORT)


def test_live_combination_is_accepted() -> None:
    settings = _make(live_trading=True, ibkr_port=IBKR_LIVE_PORT)
    assert settings.is_live is True


def test_log_defaults() -> None:
    settings = _make()
    assert settings.log_level == LogLevel.INFO
    assert settings.log_format == LogFormat.JSON
