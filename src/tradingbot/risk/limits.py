"""Hardcoded safety caps for the runtime configuration.

CLAUDE.md §10 forbids "making the risk manager configurable to less
restrictive values than the hardcoded fallbacks". This module IS the
hardcoded fallback. The web API's config schema validates every
incoming payload against these caps; any value outside the allowed
range is refused with HTTP 422.

To change a cap, edit this file (and the test that pins it). Treat
each change as a P0 review.
"""

from __future__ import annotations

from decimal import Decimal

# --- Capital and sizing --------------------------------------------------
# Absolute upper bound on a single position notional. Even on a much
# larger account the operator cannot push past this without a code change.
MAX_POSITION_SIZE_USD_CEILING: Decimal = Decimal("100000")

# --- Per-trade ----------------------------------------------------------
STOP_LOSS_PCT_CEILING: Decimal = Decimal("2.0")  # at most 2% of entry price
MAX_PROFIT_PER_TRADE_USD_CEILING: Decimal = Decimal("5000")
MIN_R_MULTIPLE_FLOOR: Decimal = Decimal("1.0")  # never accept worse than 1:1
MAX_COMMISSION_PCT_OF_TARGET_CEILING: Decimal = Decimal("20.0")

# --- Daily caps (circuit-breaker triggers) ------------------------------
MAX_DAILY_LOSS_USD_CEILING: Decimal = Decimal("5000")
MAX_TRADES_PER_DAY_CEILING: int = 100
MAX_ORDERS_PER_MINUTE_CEILING: int = 60

# --- Market microstructure ----------------------------------------------
MAX_SPREAD_BPS_CEILING: int = 100  # 1%
