"""Unit tests for `KillSwitch`."""

from __future__ import annotations

from tradingbot.monitoring.kill_switch import KillSwitch


def test_default_is_idle() -> None:
    ks = KillSwitch()
    assert ks.is_tripped() is False
    assert ks.reason is None


def test_trip_records_reason() -> None:
    ks = KillSwitch()
    ks.trip("telegram")
    assert ks.is_tripped() is True
    assert ks.reason == "telegram"


def test_trip_is_idempotent_and_keeps_first_reason() -> None:
    ks = KillSwitch()
    ks.trip("telegram")
    ks.trip("web")
    assert ks.reason == "telegram"


def test_reset_clears_state() -> None:
    ks = KillSwitch()
    ks.trip("telegram")
    ks.reset(actor="operator")
    assert ks.is_tripped() is False
    assert ks.reason is None


def test_initially_tripped_at_startup() -> None:
    ks = KillSwitch(initially_tripped=True)
    assert ks.is_tripped() is True
    assert ks.reason == "startup"
