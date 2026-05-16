"""Unit tests for `AccountStateLogger`.

We swap out `IBClient` for a stub that exposes the two methods the
logger uses: `is_connected()` and `get_account_summary()`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import cast

import pytest

from tradingbot.connector.account_state import (
    ACCOUNT_BUYING_POWER,
    ACCOUNT_NET_LIQUIDATION,
    ACCOUNT_TOTAL_CASH,
    AccountStateLogger,
)
from tradingbot.connector.ib_client import IBClient


@dataclass
class FakeIBClient:
    connected: bool = False
    summary: dict[str, str] = field(default_factory=dict)

    def is_connected(self) -> bool:
        return self.connected

    def get_account_summary(self) -> dict[str, str]:
        return dict(self.summary) if self.connected else {}


def _logger(fake: FakeIBClient, interval: float = 0.01) -> AccountStateLogger:
    return AccountStateLogger(cast(IBClient, fake), interval_seconds=interval)


@pytest.mark.asyncio
async def test_run_stops_promptly_on_stop() -> None:
    fake = FakeIBClient(connected=False)
    logger = _logger(fake, interval=10.0)

    async def stop_soon() -> None:
        await asyncio.sleep(0.01)
        logger.stop()

    await asyncio.wait_for(
        asyncio.gather(logger.run(), stop_soon()),
        timeout=1.0,
    )


def test_snapshot_skips_when_disconnected() -> None:
    fake = FakeIBClient(connected=False, summary={"NetLiquidation": "150000.00"})
    logger = _logger(fake)
    # _snapshot is a private but stable hook; calling it directly
    # avoids the asyncio plumbing.
    logger._snapshot()
    # Nothing crashes; gauge is unchanged from default 0.


def test_snapshot_sets_gauges_for_known_tags() -> None:
    fake = FakeIBClient(
        connected=True,
        summary={
            "NetLiquidation": "150000.00",
            "BuyingPower": "600000.00",
            "TotalCashValue": "150000.00",
            "UnusedTag": "ignored",
        },
    )
    logger = _logger(fake)
    logger._snapshot()
    # Internal Gauge state: pull via the private `_value` member which
    # is the documented way to read a Gauge in tests.
    assert ACCOUNT_NET_LIQUIDATION._value.get() == 150000.0
    assert ACCOUNT_BUYING_POWER._value.get() == 600000.0
    assert ACCOUNT_TOTAL_CASH._value.get() == 150000.0


def test_snapshot_handles_unparseable_value_without_crashing() -> None:
    fake = FakeIBClient(
        connected=True,
        summary={"NetLiquidation": "not-a-number"},
    )
    logger = _logger(fake)
    logger._snapshot()  # logs a warning but does not raise


def test_snapshot_empty_summary_when_connected() -> None:
    fake = FakeIBClient(connected=True, summary={})
    logger = _logger(fake)
    logger._snapshot()  # no error
