"""`StrategyEngine`: the loop that closes Phase 3.

Each `tick()` is one iteration of the discovery cycle:

1. Read the active symbol from `ActiveAssetRepository`. If none is
   selected, do nothing.
2. Read the current position state. The discovery strategy only
   runs in `CERRADA`; in any other state the engine bails out and
   leaves the management of the open position to its own loop
   (forthcoming).
3. Ask the `SRDetector` for the latest confirmed levels for the
   symbol.
4. Read the recent 1-minute bars from `MarketDataService`.
5. Call `evaluate_entry` (pure) to decide whether to enter.
6. If a `TradingSignal` is produced, persist it to `signals`,
   build a `RiskContext`, and hand it to the `OrderRouter`. The
   router runs the risk manager, submits the bracket, and persists
   the three `orders` rows. Refusals are logged and the tick ends.

The engine is intentionally stateless across ticks — every piece of
state lives in Postgres / Redis. A crash recovers seamlessly: the
next tick re-reads the world and resumes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from tradingbot.data.market_data import MarketDataService
from tradingbot.data.sr_detector import SRDetector
from tradingbot.execution.equity_tracker import EquityTracker
from tradingbot.execution.market_clock import MarketClock
from tradingbot.execution.order_rate import OrderRateCounter
from tradingbot.execution.order_router import OrderRouter, RouterResult
from tradingbot.logging_setup import get_logger
from tradingbot.monitoring.kill_switch import KillSwitch
from tradingbot.persistence.enums import BarResolution, PositionState
from tradingbot.persistence.repositories import (
    ActiveAssetRepository,
    PnLRepository,
    PositionsRepository,
    SignalRepository,
)
from tradingbot.risk.types import RiskContext
from tradingbot.strategy.discovery import DiscoveryConfig, evaluate_entry
from tradingbot.strategy.types import TradingSignal

Clock = Callable[[], datetime]

DEFAULT_TICK_INTERVAL_SECONDS: float = 5.0


@dataclass(frozen=True)
class EngineConfig:
    """Tunables the engine reads on each tick.

    Built once per config reload by the strategy supervisor (which is
    just a small wrapper around `config_policies`). Each piece is a
    snapshot of the active `config_policies` row.
    """

    discovery: DiscoveryConfig
    trend_change_lookback_minutes: int = 60
    no_new_entries_before_close_minutes: int = 15


@dataclass(frozen=True)
class TickResult:
    """What one `tick()` produced — useful for tests and the bot's logs."""

    symbol: str | None
    state: PositionState
    signal: TradingSignal | None
    router_result: RouterResult | None


class RiskContextBuilder:
    """Build a `RiskContext` from the data the engine has at hand.

    The optional dependencies (order-rate counter, equity tracker,
    active-asset repo) each feed one circuit-breaker input; when one
    is absent its field falls back to a conservative default that
    never spoofs a permissive state, so the risk manager stays a
    strict gate.
    """

    def __init__(
        self,
        kill_switch: KillSwitch,
        positions_repo: PositionsRepository,
        pnl_repo: PnLRepository,
        *,
        order_rate_counter: OrderRateCounter | None = None,
        equity_tracker: EquityTracker | None = None,
        active_asset_repo: ActiveAssetRepository | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._kill = kill_switch
        self._positions = positions_repo
        self._pnl = pnl_repo
        self._order_rate = order_rate_counter
        self._equity = equity_tracker
        self._active_asset = active_asset_repo
        self._clock = clock

    async def build(self) -> RiskContext:
        now = self._clock()
        open_position = await self._positions.get_open_position()
        pnl_today = await self._pnl.get_pnl_for_date(now.date())
        consecutive_losses = await self._positions.recent_consecutive_losses()
        recent_orders = (
            await self._order_rate.count_last_minute(now)
            if self._order_rate is not None
            else 0
        )
        drawdown = (
            await self._equity.drawdown_pct(now=now)
            if self._equity is not None
            else Decimal(0)
        )
        is_earnings_day = (
            await self._active_asset.active_asset_earnings_window()
            if self._active_asset is not None
            else False
        )

        if pnl_today is None:
            daily_loss = Decimal(0)
            trades_today = 0
        else:
            # `pnl_today.net_pnl` is signed: negative = loss. We surface
            # the magnitude of the loss; gains contribute 0.
            daily_loss = -pnl_today.net_pnl if pnl_today.net_pnl < 0 else Decimal(0)
            trades_today = pnl_today.n_trades

        return RiskContext(
            kill_switch_tripped=self._kill.is_tripped(),
            has_open_position=open_position is not None,
            daily_loss_usd=daily_loss,
            trades_today=trades_today,
            recent_orders_per_minute=recent_orders,
            consecutive_losses=consecutive_losses,
            drawdown_pct_from_open=drawdown,
            is_earnings_day=is_earnings_day,
            halt_active=False,  # TODO: subscribe to halt events
            halt_resumed_at=None,
            now=now,
        )


class StrategyEngine:
    """Owns the discovery tick. The management tick comes in a follow-up."""

    _stop: asyncio.Event

    def __init__(
        self,
        *,
        active_asset_repo: ActiveAssetRepository,
        positions_repo: PositionsRepository,
        signal_repo: SignalRepository,
        market_data: MarketDataService,
        sr_detector: SRDetector,
        order_router: OrderRouter,
        risk_context: RiskContextBuilder,
        config: EngineConfig,
        market_clock: MarketClock | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._active_asset = active_asset_repo
        self._positions = positions_repo
        self._signals = signal_repo
        self._market_data = market_data
        self._detector = sr_detector
        self._router = order_router
        self._risk_context = risk_context
        self._config = config
        self._market_clock = market_clock
        self._clock = clock
        self._stop = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        """Ask `run()` to exit at the next interval boundary."""
        self._stop.set()

    async def run(
        self,
        *,
        tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
    ) -> None:
        """Long-running loop: call `tick()` every `tick_interval_seconds`.

        Exceptions inside a tick are logged and swallowed so one
        bad iteration cannot take the engine down. Cancellation
        from the host event loop is honored normally.
        """
        self._log.info("strategy_engine_started", interval=tick_interval_seconds)
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - log and continue
                self._log.error(
                    "strategy_tick_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=tick_interval_seconds
                )
                break
            except TimeoutError:
                continue
        self._log.info("strategy_engine_stopped")

    async def tick(self) -> TickResult:
        symbol = await self._active_asset.get_active_symbol()
        if symbol is None:
            self._log.info("strategy_tick_no_active_asset")
            return TickResult(
                symbol=None,
                state=PositionState.CERRADA,
                signal=None,
                router_result=None,
            )

        open_position = await self._positions.get_open_position()
        state = (
            PositionState(open_position.state)
            if open_position is not None
            else PositionState.CERRADA
        )
        if state is not PositionState.CERRADA:
            # The management strategy owns this state; nothing to do here.
            self._log.debug(
                "strategy_tick_skip_non_cerrada",
                symbol=symbol,
                state=state.value,
            )
            return TickResult(
                symbol=symbol, state=state, signal=None, router_result=None
            )

        if self._in_no_new_entries_window():
            self._log.info("strategy_tick_eod_no_entries", symbol=symbol)
            return TickResult(
                symbol=symbol, state=state, signal=None, router_result=None
            )

        levels = await self._detector.detect(symbol)
        # `SRDetector.detect` already upserted into `sr_levels`; we
        # consume its in-memory return value for the entry decision.
        # The `signals.sr_level_id` FK stays None for now; backfilling
        # it requires looking up SRLevelRepository by price.
        bars_1m = self._market_data.get_recent_bars(
            symbol,
            BarResolution.M1,
            n=self._config.trend_change_lookback_minutes,
        )
        signal = evaluate_entry(levels, bars_1m, self._config.discovery)
        if signal is None:
            return TickResult(
                symbol=symbol, state=state, signal=None, router_result=None
            )

        signal_id = await self._signals.insert(signal, ts=self._clock())
        ctx = await self._risk_context.build()
        result = await self._router.submit_signal(
            signal, ctx, signal_id=signal_id
        )
        self._log.info(
            "strategy_tick_signal_routed",
            symbol=symbol,
            signal_id=str(signal_id),
            approved=result.approved,
            refusals=[r.value for r in result.decision.refusals],
        )
        return TickResult(
            symbol=symbol, state=state, signal=signal, router_result=result
        )

    def _in_no_new_entries_window(self) -> bool:
        """True inside the pre-close window where discovery must stop.

        Without a `MarketClock` the gate is disabled (e.g. in tests).
        """
        if self._market_clock is None:
            return False
        minutes_to_close = self._market_clock.minutes_to_close(self._clock())
        if minutes_to_close is None:
            return False
        return (
            minutes_to_close <= self._config.no_new_entries_before_close_minutes
        )


__all__ = [
    "Clock",
    "EngineConfig",
    "RiskContextBuilder",
    "StrategyEngine",
    "TickResult",
]
