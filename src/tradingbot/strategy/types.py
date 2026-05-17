"""Strategy-layer domain types.

`TradingSignal` is the in-memory dataclass the discovery strategy
produces. The strategy engine converts it into a `RiskManager`
`OrderRequest` for approval and, if approved, into a `BracketOrder`
the order router (CAPA 3) submits to IBKR.

This is intentionally distinct from `tradingbot.persistence.models.Signal`
(the ORM row written to the `signals` table). The pure dataclass
keeps the strategy testable without touching the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from tradingbot.persistence.enums import OrderSide


@dataclass(frozen=True)
class TradingSignal:
    """A pre-risk-check entry decision from the discovery strategy."""

    symbol: str
    side: OrderSide  # BUY for long, SELL for short
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    qty: Decimal
    expected_profit_usd: Decimal
    expected_commission_usd: Decimal
    r_multiple: Decimal
    sr_level_id: UUID | None  # the level that triggered this; persisted for audit
    sr_level_strength: Decimal
    is_partial: bool  # True when entering 50% on a weak signal
