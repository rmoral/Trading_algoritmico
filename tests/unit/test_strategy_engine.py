"""Tests for `StrategyEngine.tick`.

Every dependency is replaced with a fake. The real RiskManager,
OrderRouter (over a FakeBracketSubmitter), and `evaluate_entry` run
end-to-end so the test catches integration regressions between them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from tradingbot.data import CompletedBar, DetectedLevel
from tradingbot.data.sr_strength import StrengthComponents
from tradingbot.execution import MarketClock, OrderRouter
from tradingbot.execution.bracket import EntryLegSpec, ExitLegSpec
from tradingbot.execution.order_router import (
    SubmittedBracket,
    SubmittedLeg,
)
from tradingbot.monitoring.kill_switch import KillSwitch
from tradingbot.persistence.enums import BarResolution, PositionSide, PositionState, SRKind
from tradingbot.persistence.models import Position
from tradingbot.risk import RiskLimits, RiskManager
from tradingbot.risk.types import RiskRefusalReason
from tradingbot.strategy.discovery import DiscoveryConfig
from tradingbot.strategy.engine import (
    EngineConfig,
    RiskContextBuilder,
    StrategyEngine,
    TickResult,
)
from tradingbot.strategy.sizing import SizingConfig
from tradingbot.strategy.types import TradingSignal

# ---------- fakes ----------


class FakeActiveAssetRepo:
    def __init__(
        self,
        symbol: str | None = "AAPL",
        *,
        is_earnings_window: bool = False,
    ) -> None:
        self.symbol = symbol
        self._is_earnings_window = is_earnings_window

    async def get_active_symbol(self) -> str | None:
        return self.symbol

    async def active_asset_earnings_window(self) -> bool:
        return self._is_earnings_window


class FakePositionsRepo:
    def __init__(
        self,
        open_position: Position | None = None,
        *,
        consecutive_losses: int = 0,
    ) -> None:
        self.open_position = open_position
        self.created: list[UUID] = []
        self.cancelled: list[UUID] = []
        self._consecutive_losses = consecutive_losses

    async def get_open_position(self) -> Position | None:
        return self.open_position

    async def recent_consecutive_losses(self) -> int:
        return self._consecutive_losses

    async def create_opening(self, **_kwargs: object) -> UUID:
        position_id = uuid4()
        self.created.append(position_id)
        return position_id

    async def mark_cancelled(self, position_id: UUID, **_kwargs: object) -> None:
        self.cancelled.append(position_id)


class FakePnLRepo:
    def __init__(self, record: object | None = None) -> None:
        self.record = record

    async def get_pnl_for_date(self, _day: object) -> object | None:
        return self.record


class FakeSignalRepo:
    def __init__(self) -> None:
        self.inserted: list[TradingSignal] = []
        self.last_id: UUID | None = None

    async def insert(
        self, signal: TradingSignal, *, ts: datetime, **_kwargs: object
    ) -> UUID:
        self.inserted.append(signal)
        new_id = uuid4()
        self.last_id = new_id
        return new_id


class FakeMarketData:
    def __init__(self, bars: list[CompletedBar]) -> None:
        self.bars = bars

    def get_recent_bars(
        self, _symbol: str, _resolution: BarResolution, n: int | None = None
    ) -> list[CompletedBar]:
        return self.bars if n is None else self.bars[-n:]


class FakeSRDetector:
    def __init__(self, levels: list[DetectedLevel]) -> None:
        self.levels = levels
        self.calls = 0

    async def detect(self, _symbol: str) -> list[DetectedLevel]:
        self.calls += 1
        return list(self.levels)


class FakeSubmitter:
    def __init__(self) -> None:
        self.calls: list[tuple[EntryLegSpec, ExitLegSpec, ExitLegSpec]] = []
        self._next_id = 1000

    async def submit_bracket(
        self,
        entry: EntryLegSpec,
        stop_loss: ExitLegSpec,
        take_profit: ExitLegSpec,
    ) -> SubmittedBracket:
        self.calls.append((entry, stop_loss, take_profit))
        ids = [self._next_id, self._next_id + 1, self._next_id + 2]
        self._next_id += 3
        return SubmittedBracket(
            entry=SubmittedLeg(internal_id=uuid4(), ib_order_id=ids[0]),
            stop_loss=SubmittedLeg(internal_id=uuid4(), ib_order_id=ids[1]),
            take_profit=SubmittedLeg(internal_id=uuid4(), ib_order_id=ids[2]),
        )


def _fake_session_factory() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    class _Ctx:
        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    factory = MagicMock(side_effect=lambda: _Ctx())
    factory.session = session
    return factory


# ---------- helpers ----------


def _limits() -> RiskLimits:
    return RiskLimits(
        max_position_size_usd=Decimal("50000"),
        stop_loss_pct=Decimal("0.5"),
        min_r_multiple=Decimal("1.5"),
        max_commission_pct_of_target=Decimal("5"),
        max_daily_loss_usd=Decimal("2250"),
        max_trades_per_day=50,
        max_orders_per_minute=30,
        min_spread_bps=0,
        max_spread_bps=20,
        forbidden_tickers=(),
        earnings_blackout=True,
        halt_resume_cooldown_seconds=60,
        consecutive_losses_limit=5,
        drawdown_pct_from_open=Decimal("1.5"),
    )


def _config() -> EngineConfig:
    return EngineConfig(
        discovery=DiscoveryConfig(
            strong_threshold=Decimal("70"),
            weak_threshold=Decimal("40"),
            partial_entry_pct=Decimal("50"),
            proximity_tolerance_pct=Decimal("0.5"),
            rejection_tolerance_pct=Decimal("0.1"),
            sizing=SizingConfig(
                stop_loss_pct=Decimal("0.5"),
                min_profit_per_trade_usd=Decimal("100"),
                max_profit_per_trade_usd=Decimal("500"),
                min_r_multiple=Decimal("1.5"),
                max_commission_pct_of_target=Decimal("5"),
                max_position_size_usd=Decimal("50000"),
            ),
        ),
        trend_change_lookback_minutes=60,
    )


def _level(
    *,
    kind: SRKind,
    price: str,
    strength: str,
) -> DetectedLevel:
    return DetectedLevel(
        symbol="AAPL",
        kind=kind,
        price=Decimal(price),
        strength=Decimal(strength),
        components=StrengthComponents(
            clean_touches=Decimal("100"),
            volume_at_price=Decimal("60"),
            ma_confluence=Decimal("75"),
            persistence=Decimal("50"),
            rejection_quality=Decimal("40"),
        ),
        detected_at=datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    )


def _rejection_bar() -> CompletedBar:
    return CompletedBar(
        symbol="AAPL",
        resolution=BarResolution.M1,
        ts=datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
        open=Decimal("100.5"),
        high=Decimal("101.05"),
        low=Decimal("100.4"),
        close=Decimal("100.4"),
        volume=Decimal("1000"),
        wap=Decimal("100.4"),
    )


def _position_in_state(state: PositionState) -> Position:
    return Position(
        id=uuid4(),
        opened_at=datetime(2026, 5, 16, 14, 0, tzinfo=UTC),
        closed_at=None,
        symbol="AAPL",
        side=PositionSide.LONG.value,
        qty=Decimal("100"),
        avg_entry_price=Decimal("150"),
        state=state.value,
        realized_pnl=Decimal("0"),
        commissions=Decimal("0"),
    )


def _engine(
    *,
    active_symbol: str | None = "AAPL",
    open_position: Position | None = None,
    levels: list[DetectedLevel] | None = None,
    bars: list[CompletedBar] | None = None,
    kill_tripped: bool = False,
    market_clock: MarketClock | None = None,
    is_earnings_window: bool = False,
    now: datetime = datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
) -> tuple[StrategyEngine, FakeSubmitter, FakeSignalRepo]:
    if levels is None:
        levels = [
            _level(kind=SRKind.RESISTANCE, price="101", strength="80"),
            _level(kind=SRKind.SUPPORT, price="99.5", strength="80"),
        ]
    if bars is None:
        bars = [_rejection_bar()]
    kill_switch = KillSwitch(initially_tripped=kill_tripped)
    positions = FakePositionsRepo(open_position)
    pnl = FakePnLRepo()
    signals = FakeSignalRepo()
    submitter = FakeSubmitter()
    factory = _fake_session_factory()
    risk = RiskManager(_limits())
    router = OrderRouter(submitter, risk, factory, positions)
    active_asset = FakeActiveAssetRepo(
        active_symbol, is_earnings_window=is_earnings_window
    )
    context = RiskContextBuilder(
        kill_switch,
        cast("object", positions),  # type: ignore[arg-type]
        cast("object", pnl),  # type: ignore[arg-type]
        active_asset_repo=cast("object", active_asset),  # type: ignore[arg-type]
        clock=lambda: now,
    )
    engine = StrategyEngine(
        active_asset_repo=cast("object", active_asset),  # type: ignore[arg-type]
        positions_repo=cast("object", positions),  # type: ignore[arg-type]
        signal_repo=cast("object", signals),  # type: ignore[arg-type]
        market_data=cast("object", FakeMarketData(bars)),  # type: ignore[arg-type]
        sr_detector=cast("object", FakeSRDetector(levels)),  # type: ignore[arg-type]
        order_router=router,
        risk_context=context,
        config=_config(),
        market_clock=market_clock,
        clock=lambda: now,
    )
    return engine, submitter, signals


# ---------- tests ----------


@pytest.mark.asyncio
async def test_tick_no_active_asset_is_noop() -> None:
    engine, submitter, signals = _engine(active_symbol=None)
    result = await engine.tick()
    assert isinstance(result, TickResult)
    assert result.symbol is None
    assert result.signal is None
    assert result.router_result is None
    assert signals.inserted == []
    assert submitter.calls == []


@pytest.mark.asyncio
async def test_tick_skips_when_position_is_not_cerrada() -> None:
    """An ABIERTA / ABRIENDO / CERRANDO row blocks discovery."""
    for state in (
        PositionState.ABRIENDO,
        PositionState.ABIERTA,
        PositionState.CERRANDO,
    ):
        engine, submitter, signals = _engine(
            open_position=_position_in_state(state)
        )
        result = await engine.tick()
        assert result.state == state
        assert result.signal is None
        assert result.router_result is None
        assert signals.inserted == []
        assert submitter.calls == []


@pytest.mark.asyncio
async def test_tick_emits_signal_and_submits_bracket() -> None:
    """End-to-end happy path: detector -> evaluate_entry -> router."""
    engine, submitter, signals = _engine()
    result = await engine.tick()
    assert result.state == PositionState.CERRADA
    assert result.signal is not None
    assert result.router_result is not None
    assert result.router_result.approved is True
    # Signal was persisted before submission.
    assert len(signals.inserted) == 1
    # Broker received one bracket.
    assert len(submitter.calls) == 1


@pytest.mark.asyncio
async def test_tick_suppresses_entries_in_eod_window() -> None:
    """No new entries once inside the pre-close window (CLAUDE.md §6)."""
    # 19:50 UTC = 15:50 EDT -> 10 minutes to close, inside the
    # default 15-minute no-new-entries window.
    engine, submitter, signals = _engine(
        market_clock=MarketClock(),
        now=datetime(2026, 5, 16, 19, 50, tzinfo=UTC),
    )
    result = await engine.tick()
    assert result.state == PositionState.CERRADA
    assert result.signal is None
    assert result.router_result is None
    # The setup that would normally fire is suppressed: nothing routed.
    assert signals.inserted == []
    assert submitter.calls == []


@pytest.mark.asyncio
async def test_tick_with_no_setup_emits_nothing() -> None:
    """A non-rejection bar -> no signal, no DB write, no broker call."""
    flat_bar = CompletedBar(
        symbol="AAPL",
        resolution=BarResolution.M1,
        ts=datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("100.01"),
        low=Decimal("99.99"),
        close=Decimal("100"),
        volume=Decimal("100"),
        wap=Decimal("100"),
    )
    engine, submitter, signals = _engine(bars=[flat_bar])
    result = await engine.tick()
    assert result.signal is None
    assert result.router_result is None
    assert signals.inserted == []
    assert submitter.calls == []


@pytest.mark.asyncio
async def test_tick_signal_refused_by_risk_does_not_persist_orders() -> None:
    """Kill switch tripped -> signal persisted, broker NEVER called."""
    engine, submitter, signals = _engine(kill_tripped=True)
    result = await engine.tick()
    assert result.signal is not None  # discovery still produced one
    assert result.router_result is not None
    assert result.router_result.approved is False
    # The signal row was inserted (audit trail) but the bracket was not.
    assert len(signals.inserted) == 1
    assert submitter.calls == []


@pytest.mark.asyncio
async def test_tick_refused_when_asset_in_earnings_window() -> None:
    """An asset flagged inside an earnings blackout is refused."""
    engine, submitter, signals = _engine(is_earnings_window=True)
    result = await engine.tick()
    assert result.signal is not None  # discovery still fires
    assert result.router_result is not None
    assert result.router_result.approved is False
    assert (
        RiskRefusalReason.EARNINGS_BLACKOUT
        in result.router_result.decision.refusals
    )
    # Signal persisted for the audit trail, but no bracket submitted.
    assert len(signals.inserted) == 1
    assert submitter.calls == []


@pytest.mark.asyncio
async def test_risk_context_builder_returns_loss_magnitude() -> None:
    """A net loss yesterday surfaces as a positive `daily_loss_usd`."""

    class _PnLRow:
        net_pnl = Decimal("-300")
        n_trades = 3

    kill = KillSwitch()
    positions = FakePositionsRepo()
    pnl = FakePnLRepo(_PnLRow())
    builder = RiskContextBuilder(
        kill,
        cast("object", positions),  # type: ignore[arg-type]
        cast("object", pnl),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    )
    ctx = await builder.build()
    assert ctx.daily_loss_usd == Decimal("300")
    assert ctx.trades_today == 3
    assert ctx.has_open_position is False


@pytest.mark.asyncio
async def test_risk_context_builder_reads_halt_state() -> None:
    """An active halt from the halt store surfaces on the context."""

    class _FakeHaltStore:
        async def state(self) -> tuple[bool, None]:
            return True, None

    kill = KillSwitch()
    positions = FakePositionsRepo()
    pnl = FakePnLRepo()
    builder = RiskContextBuilder(
        kill,
        cast("object", positions),  # type: ignore[arg-type]
        cast("object", pnl),  # type: ignore[arg-type]
        halt_store=cast("object", _FakeHaltStore()),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    )
    ctx = await builder.build()
    assert ctx.halt_active is True


@pytest.mark.asyncio
async def test_risk_context_builder_clamps_gains_to_zero() -> None:
    """A net gain today -> daily_loss_usd is 0, not negative."""

    class _PnLRow:
        net_pnl = Decimal("200")
        n_trades = 2

    kill = KillSwitch()
    positions = FakePositionsRepo()
    pnl = FakePnLRepo(_PnLRow())
    builder = RiskContextBuilder(
        kill,
        cast("object", positions),  # type: ignore[arg-type]
        cast("object", pnl),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    )
    ctx = await builder.build()
    assert ctx.daily_loss_usd == Decimal(0)


@pytest.mark.asyncio
async def test_risk_context_builder_reads_kill_switch_and_position() -> None:
    kill = KillSwitch(initially_tripped=True)
    positions = FakePositionsRepo(_position_in_state(PositionState.ABIERTA))
    pnl = FakePnLRepo()
    builder = RiskContextBuilder(
        kill,
        cast("object", positions),  # type: ignore[arg-type]
        cast("object", pnl),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 5, 16, 14, 30, tzinfo=UTC),
    )
    ctx = await builder.build()
    assert ctx.kill_switch_tripped is True
    assert ctx.has_open_position is True


@pytest.mark.asyncio
async def test_run_loops_until_stop() -> None:
    """`run()` keeps calling `tick()` until `stop()` is set."""
    engine, _, _ = _engine()
    ticks_seen: list[int] = []
    original_tick = engine.tick

    async def counting_tick() -> TickResult:
        ticks_seen.append(1)
        return await original_tick()

    engine.tick = counting_tick  # type: ignore[method-assign]

    async def stop_after_ticks() -> None:
        for _ in range(200):
            if len(ticks_seen) >= 2:
                break
            await asyncio.sleep(0.005)
        engine.stop()

    await asyncio.wait_for(
        asyncio.gather(
            engine.run(tick_interval_seconds=0.01),
            stop_after_ticks(),
        ),
        timeout=2.0,
    )
    assert len(ticks_seen) >= 2


@pytest.mark.asyncio
async def test_run_swallows_tick_exceptions() -> None:
    """A raising tick is logged but the loop continues."""
    engine, _, _ = _engine()
    calls = 0

    async def flaky_tick() -> TickResult:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("boom")
        return TickResult(
            symbol=None,
            state=PositionState.CERRADA,
            signal=None,
            router_result=None,
        )

    engine.tick = flaky_tick  # type: ignore[method-assign]

    async def stop_after() -> None:
        for _ in range(200):
            if calls >= 3:
                break
            await asyncio.sleep(0.005)
        engine.stop()

    await asyncio.wait_for(
        asyncio.gather(
            engine.run(tick_interval_seconds=0.005),
            stop_after(),
        ),
        timeout=2.0,
    )
    assert calls >= 3


