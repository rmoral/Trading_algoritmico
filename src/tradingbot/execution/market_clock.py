"""`MarketClock`: how long until the US equity session closes.

Two end-of-day controls depend on this:

- the strategy engine stops scanning for entries
  `no_new_entries_before_close_minutes` before the close,
- the end-of-day flattener force-closes any open position
  `force_flatten_before_close_minutes` before the close.

Regular trading hours are 09:30-16:00 America/New_York; `ZoneInfo`
handles the DST offset. `minutes_to_close` returns `None` outside
RTH so callers treat "market not open" as "no deadline".

Known v1 limitation: half-day sessions (e.g. the day after
Thanksgiving, which closes 13:00 ET) and exchange holidays are not
modelled. On a half-day the bot would flatten near 16:00 instead of
13:00. The operator only arms the bot by selecting an asset, so this
is acceptable for paper trading; a proper exchange calendar is a
follow-up.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE: str = "America/New_York"
DEFAULT_SESSION_OPEN: time = time(9, 30)
DEFAULT_SESSION_CLOSE: time = time(16, 0)

_SECONDS_PER_MINUTE: Decimal = Decimal("60")


class MarketClock:
    """Regular-trading-hours session clock for US equities."""

    def __init__(
        self,
        *,
        timezone: str = DEFAULT_TIMEZONE,
        session_open: time = DEFAULT_SESSION_OPEN,
        session_close: time = DEFAULT_SESSION_CLOSE,
    ) -> None:
        self._zone = ZoneInfo(timezone)
        self._open = session_open
        self._close = session_close

    def minutes_to_close(self, now: datetime) -> Decimal | None:
        """Minutes until today's RTH close, or `None` outside RTH.

        `now` must be timezone-aware; it is converted to the exchange
        timezone before the comparison. The result is `None` before
        the open and at/after the close.
        """
        local = now.astimezone(self._zone)
        open_dt = datetime.combine(local.date(), self._open, tzinfo=self._zone)
        close_dt = datetime.combine(local.date(), self._close, tzinfo=self._zone)
        if local < open_dt or local >= close_dt:
            return None
        remaining_seconds = Decimal((close_dt - local).total_seconds())
        return remaining_seconds / _SECONDS_PER_MINUTE


__all__ = ["MarketClock"]
