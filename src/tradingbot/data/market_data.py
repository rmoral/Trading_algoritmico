"""`MarketDataService`: subscribe / unsubscribe / rolling buffer.

For each subscribed symbol the service spawns one task per resolution
(1 min, 5 min, 15 min). Each task consumes the `BarSource` stream,
persists every completed bar through the `BarSink`, and appends it
to a bounded in-memory deque so consumers (indicators, S/R detector)
can read a recent window without hitting the database.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from contextlib import suppress

from tradingbot.data.bars import BarSink, BarSource, CompletedBar
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import BarResolution

DEFAULT_BUFFER_SIZE: int = 200
DEFAULT_RESOLUTIONS: tuple[BarResolution, ...] = (
    BarResolution.M1,
    BarResolution.M5,
    BarResolution.M15,
)


class MarketDataService:
    """Owns the bar subscriptions for the currently active symbol(s).

    The state machine guarantees the bot operates on a single symbol
    at a time, but the service supports multiple in principle so
    tests can interleave streams.
    """

    def __init__(
        self,
        source: BarSource,
        sink: BarSink,
        *,
        resolutions: Iterable[BarResolution] = DEFAULT_RESOLUTIONS,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> None:
        self._source = source
        self._sink = sink
        self._resolutions: tuple[BarResolution, ...] = tuple(resolutions)
        self._buffer_size = buffer_size
        self._buffers: dict[tuple[str, BarResolution], deque[CompletedBar]] = {}
        self._tasks: dict[tuple[str, BarResolution], asyncio.Task[None]] = {}
        self._log = get_logger(__name__)

    async def subscribe(self, symbol: str) -> None:
        """Start streaming the configured resolutions for `symbol`.

        Idempotent: a second call for the same symbol is a no-op.
        """
        for resolution in self._resolutions:
            key = (symbol, resolution)
            if key in self._tasks:
                continue
            self._buffers[key] = deque(maxlen=self._buffer_size)
            self._tasks[key] = asyncio.create_task(
                self._consume(symbol, resolution),
                name=f"market_data:{symbol}:{resolution.value}",
            )
        self._log.info(
            "market_data_subscribed",
            symbol=symbol,
            resolutions=[r.value for r in self._resolutions],
        )

    async def unsubscribe(self, symbol: str) -> None:
        """Cancel all tasks for `symbol` and drop its buffers."""
        for resolution in self._resolutions:
            key = (symbol, resolution)
            task = self._tasks.pop(key, None)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._buffers.pop(key, None)
        self._log.info("market_data_unsubscribed", symbol=symbol)

    async def stop(self) -> None:
        """Cancel every subscription. Safe to call multiple times."""
        symbols = {key[0] for key in self._tasks}
        for symbol in symbols:
            await self.unsubscribe(symbol)

    def get_recent_bars(
        self,
        symbol: str,
        resolution: BarResolution,
        n: int | None = None,
    ) -> list[CompletedBar]:
        """Return the most recent buffered bars in chronological order.

        Returns an empty list when no subscription exists yet.
        """
        buf = self._buffers.get((symbol, resolution))
        if buf is None:
            return []
        if n is None:
            return list(buf)
        return list(buf)[-n:]

    def is_subscribed(self, symbol: str) -> bool:
        return any(key[0] == symbol for key in self._tasks)

    async def _consume(self, symbol: str, resolution: BarResolution) -> None:
        try:
            async for bar in self._source.stream(symbol, resolution):
                self._buffers[(symbol, resolution)].append(bar)
                try:
                    await self._sink.insert_bar(bar)
                except Exception as exc:  # noqa: BLE001 - log and continue
                    self._log.error(
                        "bar_persist_failed",
                        symbol=symbol,
                        resolution=resolution.value,
                        ts=bar.ts.isoformat(),
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - top-level guard
            self._log.error(
                "market_data_stream_failed",
                symbol=symbol,
                resolution=resolution.value,
                error=str(exc),
                error_type=type(exc).__name__,
            )
