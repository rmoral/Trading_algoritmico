"""Smoke tests for `tradingbot.logging_setup`.

Verify that the JSON formatter actually produces parseable JSON with
the expected fields, and that the console formatter does not crash.
Idempotence is also checked: calling `configure_logging` twice must
leave a single handler attached to the root logger.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.settings import LogFormat, LogLevel, Settings


def _settings(log_format: LogFormat) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        log_format=log_format,
        log_level=LogLevel.INFO,
    )


def test_json_format_emits_parseable_record(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(_settings(LogFormat.JSON))
    log = get_logger("test")
    log.info("hello", ticker="AAPL", side="LONG")

    captured = capsys.readouterr().out.strip().splitlines()
    assert captured, "no log line captured"
    record: dict[str, Any] = json.loads(captured[-1])

    assert record["event"] == "hello"
    assert record["ticker"] == "AAPL"
    assert record["side"] == "LONG"
    assert record["level"] == "info"
    assert record["logger"] == "test"
    assert "timestamp" in record


def test_console_format_does_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(_settings(LogFormat.CONSOLE))
    get_logger("test").info("hello console")
    out = capsys.readouterr().out
    assert "hello console" in out


def test_configure_logging_is_idempotent() -> None:
    configure_logging(_settings(LogFormat.JSON))
    configure_logging(_settings(LogFormat.JSON))
    assert len(logging.getLogger().handlers) == 1
