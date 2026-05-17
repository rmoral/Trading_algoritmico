"""Wire types for the risk manager.

`RiskManager.approve()` is pure: it consumes an `OrderRequest`
(what we are about to submit), a `RiskContext` (the world's current
state), and a `RiskLimits` (snapshot of the runtime config). The
output is a `RiskDecision`. No I/O happens inside.

The strategy / execution layers are responsible for assembling
these from Postgres + Redis + IBKR snapshots before each approval
call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from tradingbot.persistence.enums import OrderSide, OrderType


class RiskRefusalReason(StrEnum):
    """Reasons the risk manager can refuse an order.

    Hard refusals only. Soft warnings (e.g. "approaching the daily
    loss cap") use plain strings.
    """

    INVALID_ORDER = "INVALID_ORDER"
    KILL_SWITCH = "KILL_SWITCH"
    HAS_OPEN_POSITION = "HAS_OPEN_POSITION"
    FORBIDDEN_TICKER = "FORBIDDEN_TICKER"
    EARNINGS_BLACKOUT = "EARNINGS_BLACKOUT"
    HALT_COOLDOWN = "HALT_COOLDOWN"
    DAILY_LOSS_CAP = "DAILY_LOSS_CAP"
    MAX_TRADES_PER_DAY = "MAX_TRADES_PER_DAY"
    MAX_ORDERS_PER_MINUTE = "MAX_ORDERS_PER_MINUTE"
    POSITION_SIZE_CAP = "POSITION_SIZE_CAP"
    STOP_LOSS_PCT_EXCEEDED = "STOP_LOSS_PCT_EXCEEDED"
    R_MULTIPLE_TOO_LOW = "R_MULTIPLE_TOO_LOW"
    COMMISSION_TOO_HIGH = "COMMISSION_TOO_HIGH"
    SPREAD_TOO_NARROW = "SPREAD_TOO_NARROW"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    DRAWDOWN_EXCEEDED = "DRAWDOWN_EXCEEDED"


@dataclass(frozen=True)
class OrderRequest:
    """A would-be order, captured pre-submission.

    The risk manager reads these fields. The order router (CAPA 3)
    will convert an approved request into the underlying IBKR
    bracket. `entry_price` is the price the discovery strategy is
    aiming to enter at — for a marketable limit, this is typically
    the limit price; for a flatten it can be the last traded price.
    """

    symbol: str
    side: OrderSide
    qty: Decimal
    order_type: OrderType
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    estimated_commission_usd: Decimal  # round-trip estimate
    bid: Decimal | None = None
    ask: Decimal | None = None


@dataclass(frozen=True)
class RiskContext:
    """Snapshot of state the risk manager needs to decide.

    Built once per approval call by the strategy layer:
    - `kill_switch_tripped` from `KillSwitch.is_tripped()`.
    - `has_open_position` from `PositionsRepository.get_open_position()`.
    - `daily_loss_usd` is the absolute (positive) amount lost today,
      0 when flat or up.
    - `trades_today` from `PnLDaily.n_trades`.
    - `recent_orders_per_minute` from a rolling counter the
      execution layer maintains.
    - `consecutive_losses` from a counter the strategy maintains.
    - `drawdown_pct_from_open` = how far the account equity is below
      its session-open peak, as a positive percentage.
    - `now` is passed in so tests inject a clock.
    """

    kill_switch_tripped: bool
    has_open_position: bool
    daily_loss_usd: Decimal
    trades_today: int
    recent_orders_per_minute: int
    consecutive_losses: int
    drawdown_pct_from_open: Decimal
    is_earnings_day: bool
    halt_active: bool
    halt_resumed_at: datetime | None
    now: datetime


@dataclass(frozen=True)
class RiskLimits:
    """Runtime config the risk manager honours.

    Built from the current `config_policies` row. The hardcoded
    ceilings in `tradingbot.risk.limits` are stricter caps that the
    API schema enforces before any of these values can be stored,
    so the manager treats these inputs as already-bounded.
    """

    max_position_size_usd: Decimal
    stop_loss_pct: Decimal  # percent units (0.5 means 0.5%)
    min_r_multiple: Decimal
    max_commission_pct_of_target: Decimal  # percent (5 means 5%)
    max_daily_loss_usd: Decimal
    max_trades_per_day: int
    max_orders_per_minute: int
    min_spread_bps: int
    max_spread_bps: int
    forbidden_tickers: tuple[str, ...]
    earnings_blackout: bool
    halt_resume_cooldown_seconds: int
    consecutive_losses_limit: int
    drawdown_pct_from_open: Decimal  # percent units


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of one approval call.

    - `approved` is True iff `refusals` is empty.
    - `warnings` are soft-limit notifications the strategy may
      surface to Telegram / the dashboard but they do NOT block
      the trade.
    """

    refusals: tuple[RiskRefusalReason, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approved(self) -> bool:
        return len(self.refusals) == 0
