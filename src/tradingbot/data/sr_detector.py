"""S/R detector orchestrator.

One `detect(symbol)` call:

1. Pulls the most recent 5-minute and 15-minute bars from
   `MarketDataService` for the configured `sr_lookback_minutes`.
2. Pulls the long 1-minute buffer for indicator warm-up
   (`indicator_history_minutes` worth of bars).
3. Computes the `IndicatorSnapshot` (session VWAP, EMA20/50/200).
4. Runs `find_pivots` on each higher-timeframe series, then
   `cross_confirm` to drop any 15m pivot the 5m series did not back.
5. For every confirmed level computes the strength composite and
   upserts the result through an `SRLevelSink`.

The detector is stateless: it can be called any time the strategy
loop wants a fresh snapshot. The DB row keyed by
(symbol, kind, price) holds the persistent history.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradingbot.data.bars import CompletedBar
from tradingbot.data.indicators import ema, session_vwap
from tradingbot.data.market_data import MarketDataService
from tradingbot.data.pivots import cross_confirm, find_pivots
from tradingbot.data.sr_strength import (
    DEFAULT_WEIGHTS,
    IndicatorSnapshot,
    StrengthComponents,
    StrengthWeights,
    compute_strength,
)
from tradingbot.logging_setup import get_logger
from tradingbot.persistence.enums import BarResolution, SRKind

_MINUTES_PER_BAR: dict[BarResolution, int] = {
    BarResolution.M1: 1,
    BarResolution.M5: 5,
    BarResolution.M15: 15,
}


@dataclass(frozen=True)
class SRDetectorConfig:
    """Runtime knobs the strategy loop hands the detector.

    Mirrors a subset of `config_policies`. The detector does not read
    the DB itself — the strategy layer builds this once per config
    reload and passes it in.
    """

    sr_lookback_minutes: int
    sr_pivot_window: int
    sr_level_tolerance_pct: Decimal
    indicator_history_minutes: int
    weights: StrengthWeights = DEFAULT_WEIGHTS


@dataclass(frozen=True)
class DetectedLevel:
    """The output of one detection pass for one candidate level."""

    symbol: str
    kind: SRKind
    price: Decimal
    strength: Decimal
    components: StrengthComponents
    detected_at: datetime


@runtime_checkable
class SRLevelSink(Protocol):
    """Where the detector persists results. `SRLevelRepository` implements this."""

    async def upsert_level(self, level: DetectedLevel) -> None: ...


Clock = Callable[[], datetime]


class SRDetector:
    """Stateless orchestrator over the pure functions in this layer."""

    def __init__(
        self,
        market_data: MarketDataService,
        sink: SRLevelSink,
        config: SRDetectorConfig,
        *,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._market_data = market_data
        self._sink = sink
        self._config = config
        self._clock = clock
        self._log = get_logger(__name__)

    async def detect(self, symbol: str) -> list[DetectedLevel]:
        """Run one full detection pass for `symbol`.

        Returns the list of confirmed + scored levels. The strategy
        consumes this directly; the same data has already been pushed
        to the `sr_levels` table via the sink.
        """
        bars_15m = self._slice(symbol, BarResolution.M15, self._config.sr_lookback_minutes)
        bars_5m = self._slice(symbol, BarResolution.M5, self._config.sr_lookback_minutes)
        bars_1m = self._slice(
            symbol, BarResolution.M1, self._config.indicator_history_minutes
        )

        pivots_15m = find_pivots(
            bars_15m,
            window=self._config.sr_pivot_window,
            timeframe=BarResolution.M15,
        )
        pivots_5m = find_pivots(
            bars_5m,
            window=self._config.sr_pivot_window,
            timeframe=BarResolution.M5,
        )
        confirmed = cross_confirm(
            pivots_15m,
            pivots_5m,
            tolerance_pct=self._config.sr_level_tolerance_pct,
        )

        snapshot = self._indicator_snapshot(bars_1m)
        now = self._clock()
        detected: list[DetectedLevel] = []
        for level in confirmed:
            strength, components = compute_strength(
                level.price,
                bars_1m,
                snapshot,
                tolerance_pct=self._config.sr_level_tolerance_pct,
                minutes_per_bar=_MINUTES_PER_BAR[BarResolution.M1],
                weights=self._config.weights,
            )
            d = DetectedLevel(
                symbol=symbol,
                kind=level.kind,
                price=level.price,
                strength=strength,
                components=components,
                detected_at=now,
            )
            try:
                await self._sink.upsert_level(d)
            except Exception as exc:  # noqa: BLE001 - log and continue
                self._log.error(
                    "sr_level_persist_failed",
                    symbol=symbol,
                    kind=level.kind.value,
                    price=str(level.price),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            detected.append(d)

        self._log.info(
            "sr_detection_pass",
            symbol=symbol,
            pivots_15m=len(pivots_15m),
            pivots_5m=len(pivots_5m),
            confirmed=len(confirmed),
            detected=len(detected),
        )
        return detected

    def _slice(
        self, symbol: str, resolution: BarResolution, lookback_minutes: int
    ) -> list[CompletedBar]:
        n = lookback_minutes // _MINUTES_PER_BAR[resolution]
        if n < 1:
            return []
        return self._market_data.get_recent_bars(symbol, resolution, n=n)

    def _indicator_snapshot(self, bars_1m: list[CompletedBar]) -> IndicatorSnapshot:
        return IndicatorSnapshot(
            vwap=session_vwap(bars_1m),
            ema20=ema(bars_1m, period=20),
            ema50=ema(bars_1m, period=50),
            ema200=ema(bars_1m, period=200),
        )


def components_to_jsonb(c: StrengthComponents) -> dict[str, str]:
    """Serialize `StrengthComponents` for the `sr_levels.components` JSONB column.

    Values are stored as strings so the Decimal precision survives a
    JSON round-trip. The detector and the API rebuild Decimals on
    read.
    """
    return {
        "clean_touches": str(c.clean_touches),
        "volume_at_price": str(c.volume_at_price),
        "ma_confluence": str(c.ma_confluence),
        "persistence": str(c.persistence),
        "rejection_quality": str(c.rejection_quality),
    }


__all__: list[str] = [
    "Clock",
    "DetectedLevel",
    "SRDetector",
    "SRDetectorConfig",
    "SRLevelSink",
    "components_to_jsonb",
]
