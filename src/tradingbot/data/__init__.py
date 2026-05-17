"""Market data layer (CAPA 2).

Subscribes to bars at 1 min / 5 min / 15 min for the active symbol,
streams them through `MarketDataService`, persists each completed
bar to the `bars` hypertable, and keeps a rolling in-memory buffer
for downstream consumers (indicators, S/R detector, strategy).
"""

from tradingbot.data.bars import BarSink, BarSource, CompletedBar
from tradingbot.data.indicators import (
    VolumeBucket,
    atr,
    ema,
    session_vwap,
    volume_profile,
)
from tradingbot.data.market_data import MarketDataService

__all__ = [
    "BarSink",
    "BarSource",
    "CompletedBar",
    "MarketDataService",
    "VolumeBucket",
    "atr",
    "ema",
    "session_vwap",
    "volume_profile",
]
