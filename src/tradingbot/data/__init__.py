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
from tradingbot.data.pivots import ConfirmedLevel, Pivot, cross_confirm, find_pivots
from tradingbot.data.sr_detector import (
    DetectedLevel,
    SRDetector,
    SRDetectorConfig,
    SRLevelSink,
    components_to_jsonb,
)
from tradingbot.data.sr_strength import (
    DEFAULT_WEIGHTS,
    IndicatorSnapshot,
    StrengthComponents,
    StrengthWeights,
    clean_touches_score,
    compute_strength,
    ma_confluence_score,
    persistence_score,
    rejection_quality_score,
    volume_at_price_score,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "BarSink",
    "BarSource",
    "CompletedBar",
    "ConfirmedLevel",
    "DetectedLevel",
    "IndicatorSnapshot",
    "MarketDataService",
    "Pivot",
    "SRDetector",
    "SRDetectorConfig",
    "SRLevelSink",
    "StrengthComponents",
    "StrengthWeights",
    "VolumeBucket",
    "atr",
    "clean_touches_score",
    "compute_strength",
    "components_to_jsonb",
    "cross_confirm",
    "ema",
    "find_pivots",
    "ma_confluence_score",
    "persistence_score",
    "rejection_quality_score",
    "session_vwap",
    "volume_at_price_score",
    "volume_profile",
]
