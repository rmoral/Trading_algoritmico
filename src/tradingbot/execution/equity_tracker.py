"""`EquityTracker`: session drawdown from the account equity peak.

The risk manager refuses new entries once `drawdown_pct_from_open`
exceeds its limit, but only if `RiskContext.drawdown_pct_from_open`
reflects reality. This tracker is that source of truth.

`EquityMonitor` feeds account `NetLiquidation` snapshots in via
`record`; `drawdown_pct` reports how far the latest equity sits
below the peak seen so far today, as a positive percentage.

State lives in Redis keyed by trading date: the peak resets
naturally each new day, and the value survives a bot restart so a
restart mid-session cannot silently forget a drawdown already in
progress. `NetLiquidation` already includes the open position's
unrealised P&L, so intraday drawdown tracks mark-to-market.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from redis.asyncio import Redis

DEFAULT_KEY_PREFIX: str = "tradingbot:equity"
# Two days so the previous session's keys linger harmlessly, then
# expire on their own without a sweeper.
_TTL_SECONDS: int = 2 * 24 * 60 * 60

_HUNDRED: Decimal = Decimal("100")


def _parse(raw: Any) -> Decimal | None:
    """Decode a Redis value (bytes/str) into a Decimal, or None."""
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


class EquityTracker:
    """Track the session equity peak and report drawdown from it."""

    def __init__(
        self, redis: Redis[Any], *, key_prefix: str = DEFAULT_KEY_PREFIX
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix

    def _keys(self, now: datetime) -> tuple[str, str]:
        day = now.date().isoformat()
        return f"{self._prefix}:peak:{day}", f"{self._prefix}:latest:{day}"

    async def record(self, equity: Decimal, *, now: datetime) -> None:
        """Store `equity` as the latest reading and raise the peak."""
        peak_key, latest_key = self._keys(now)
        existing_peak = _parse(await self._redis.get(peak_key))
        peak = equity if existing_peak is None else max(existing_peak, equity)
        await self._redis.set(peak_key, str(peak), ex=_TTL_SECONDS)
        await self._redis.set(latest_key, str(equity), ex=_TTL_SECONDS)

    async def drawdown_pct(self, *, now: datetime) -> Decimal:
        """Percent the latest equity is below today's peak.

        Returns 0 when no equity has been recorded yet today — the
        kill switch and the daily-loss cap still protect the account
        while the first snapshot is pending.
        """
        peak_key, latest_key = self._keys(now)
        peak = _parse(await self._redis.get(peak_key))
        latest = _parse(await self._redis.get(latest_key))
        if peak is None or latest is None or peak <= 0:
            return Decimal("0")
        drawdown = (peak - latest) / peak * _HUNDRED
        return drawdown if drawdown > 0 else Decimal("0")


__all__ = ["EquityTracker"]
