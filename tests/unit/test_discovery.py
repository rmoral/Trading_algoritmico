"""Tests for the discovery strategy's `evaluate_entry`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingbot.data import CompletedBar, DetectedLevel
from tradingbot.data.sr_strength import StrengthComponents
from tradingbot.persistence.enums import BarResolution, OrderSide, SRKind
from tradingbot.strategy import (
    DiscoveryConfig,
    SizingConfig,
    TradingSignal,
    evaluate_entry,
    is_rebound_bar,
    is_rejection_bar,
)

# ---------- helpers ----------


def _sizing_config() -> SizingConfig:
    return SizingConfig(
        stop_loss_pct=Decimal("0.5"),
        min_profit_per_trade_usd=Decimal("100"),
        max_profit_per_trade_usd=Decimal("500"),
        min_r_multiple=Decimal("1.5"),
        max_commission_pct_of_target=Decimal("5"),
        max_position_size_usd=Decimal("50000"),
    )


def _discovery_config(**overrides: object) -> DiscoveryConfig:
    base: dict[str, object] = {
        "strong_threshold": Decimal("70"),
        "weak_threshold": Decimal("40"),
        "partial_entry_pct": Decimal("50"),
        "proximity_tolerance_pct": Decimal("0.5"),
        "rejection_tolerance_pct": Decimal("0.1"),
        "sizing": _sizing_config(),
    }
    base.update(overrides)
    return DiscoveryConfig(**base)  # type: ignore[arg-type]


def _level(
    *,
    symbol: str = "AAPL",
    kind: SRKind,
    price: str,
    strength: str,
) -> DetectedLevel:
    return DetectedLevel(
        symbol=symbol,
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


def _bar(
    *,
    o: str,
    h: str,
    low: str,
    c: str,
    minute: int = 0,
) -> CompletedBar:
    return CompletedBar(
        symbol="AAPL",
        resolution=BarResolution.M1,
        ts=datetime(2026, 5, 16, 14, 30, tzinfo=UTC) + timedelta(minutes=minute),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("1000"),
        wap=Decimal(c),
    )


# ---------- is_rejection_bar / is_rebound_bar ----------


def test_rejection_bar_detected() -> None:
    """Pierces the resistance, closes back below with a bear body."""
    bar = _bar(o="100.5", h="101.05", low="100.4", c="100.4")
    assert is_rejection_bar(bar, Decimal("101"), Decimal("0.1")) is True


def test_rejection_bar_needs_bear_body() -> None:
    bar = _bar(o="100.4", h="101.05", low="100.4", c="100.5")
    assert is_rejection_bar(bar, Decimal("101"), Decimal("0.1")) is False


def test_rejection_bar_requires_reach() -> None:
    """High doesn't reach the level -> no rejection."""
    bar = _bar(o="100.5", h="100.6", low="100.4", c="100.4")
    assert is_rejection_bar(bar, Decimal("101"), Decimal("0.1")) is False


def test_rebound_bar_detected() -> None:
    """Pierces the support, closes back above with a bull body."""
    bar = _bar(o="99.5", h="99.6", low="98.95", c="99.6")
    assert is_rebound_bar(bar, Decimal("99"), Decimal("0.1")) is True


def test_rebound_bar_needs_bull_body() -> None:
    bar = _bar(o="99.6", h="99.6", low="98.95", c="99.5")
    assert is_rebound_bar(bar, Decimal("99"), Decimal("0.1")) is False


# ---------- evaluate_entry ----------


def test_no_levels_returns_none() -> None:
    bars = [_bar(o="100", h="100.5", low="99.5", c="100")]
    assert evaluate_entry([], bars, _discovery_config()) is None


def test_no_bars_returns_none() -> None:
    levels = [_level(kind=SRKind.RESISTANCE, price="101", strength="80")]
    assert evaluate_entry(levels, [], _discovery_config()) is None


def test_weak_level_below_threshold_skipped() -> None:
    levels = [_level(kind=SRKind.RESISTANCE, price="101", strength="30")]
    bars = [_bar(o="100.5", h="101.05", low="100.4", c="100.4")]
    assert evaluate_entry(levels, bars, _discovery_config()) is None


def test_resistance_rejection_short_signal() -> None:
    """Strong resistance at 101, current 100.4 with rejection bar -> SHORT."""
    levels = [
        _level(kind=SRKind.RESISTANCE, price="101", strength="80"),
        _level(kind=SRKind.SUPPORT, price="99.5", strength="80"),  # the target
    ]
    bars = [_bar(o="100.5", h="101.05", low="100.4", c="100.4")]
    signal = evaluate_entry(levels, bars, _discovery_config())
    assert isinstance(signal, TradingSignal)
    assert signal.side == OrderSide.SELL
    assert signal.entry_price == Decimal("100.4")
    # SL above entry (short) at 0.5%.
    assert signal.stop_loss_price > signal.entry_price
    # Target is the next support BELOW current price.
    assert signal.take_profit_price == Decimal("99.5")


def test_support_rebound_long_signal() -> None:
    """Strong support at 99, current 99.6 with rebound bar -> LONG."""
    levels = [
        _level(kind=SRKind.SUPPORT, price="99", strength="80"),
        _level(kind=SRKind.RESISTANCE, price="100.5", strength="80"),  # target
    ]
    bars = [_bar(o="99.5", h="99.6", low="98.95", c="99.6")]
    signal = evaluate_entry(levels, bars, _discovery_config())
    assert isinstance(signal, TradingSignal)
    assert signal.side == OrderSide.BUY
    assert signal.entry_price == Decimal("99.6")
    assert signal.stop_loss_price < signal.entry_price
    assert signal.take_profit_price == Decimal("100.5")


def test_weak_signal_emits_partial() -> None:
    """Strength 50 -> between weak and strong -> partial entry."""
    levels = [
        _level(kind=SRKind.RESISTANCE, price="101", strength="50"),
        _level(kind=SRKind.SUPPORT, price="99.5", strength="80"),
    ]
    bars = [_bar(o="100.5", h="101.05", low="100.4", c="100.4")]
    signal = evaluate_entry(levels, bars, _discovery_config())
    assert isinstance(signal, TradingSignal)
    assert signal.is_partial is True


def test_strong_signal_not_partial() -> None:
    levels = [
        _level(kind=SRKind.RESISTANCE, price="101", strength="80"),
        _level(kind=SRKind.SUPPORT, price="99.5", strength="80"),
    ]
    bars = [_bar(o="100.5", h="101.05", low="100.4", c="100.4")]
    signal = evaluate_entry(levels, bars, _discovery_config())
    assert isinstance(signal, TradingSignal)
    assert signal.is_partial is False


def test_no_target_no_signal() -> None:
    """Resistance rejection but no support below -> nothing to aim at."""
    levels = [_level(kind=SRKind.RESISTANCE, price="101", strength="80")]
    bars = [_bar(o="100.5", h="101.05", low="100.4", c="100.4")]
    assert evaluate_entry(levels, bars, _discovery_config()) is None


def test_price_already_through_level_skipped() -> None:
    """Current price above the resistance -> the level already broke."""
    levels = [
        _level(kind=SRKind.RESISTANCE, price="101", strength="80"),
        _level(kind=SRKind.SUPPORT, price="99.5", strength="80"),
    ]
    bars = [_bar(o="101.5", h="102", low="101", c="101.2")]
    assert evaluate_entry(levels, bars, _discovery_config()) is None


def test_proximity_gate_rejects_far_levels() -> None:
    """A resistance 5% above the bar's high -> outside proximity."""
    levels = [
        _level(kind=SRKind.RESISTANCE, price="106", strength="80"),
        _level(kind=SRKind.SUPPORT, price="99.5", strength="80"),
    ]
    # Bar's high tops out at 100.6 — 5%+ below the resistance.
    bars = [_bar(o="100.5", h="100.6", low="100.4", c="100.4")]
    assert evaluate_entry(levels, bars, _discovery_config()) is None


def test_strongest_level_chosen() -> None:
    """Two valid resistance setups -> the stronger one drives the signal."""
    levels = [
        _level(kind=SRKind.RESISTANCE, price="101", strength="50"),
        _level(kind=SRKind.RESISTANCE, price="100.95", strength="90"),
        _level(kind=SRKind.SUPPORT, price="99.5", strength="80"),
    ]
    # Rejection bar reaches BOTH resistance levels (high=101.05).
    bars = [_bar(o="100.5", h="101.05", low="100.4", c="100.4")]
    signal = evaluate_entry(levels, bars, _discovery_config())
    assert isinstance(signal, TradingSignal)
    # The 90-strength level wins (it is selected first by sort order).
    assert signal.sr_level_strength == Decimal("90")
    assert signal.is_partial is False
