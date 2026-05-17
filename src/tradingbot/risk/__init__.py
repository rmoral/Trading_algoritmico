"""Risk layer (CAPA 5).

`RiskManager` is the sacred gate before any order submission
(CLAUDE.md §2 principle 2). The hardcoded ceilings in `limits.py`
are the absolute caps the runtime config cannot relax (CLAUDE.md
§10).
"""

from tradingbot.risk import limits
from tradingbot.risk.manager import RiskManager
from tradingbot.risk.types import (
    OrderRequest,
    RiskContext,
    RiskDecision,
    RiskLimits,
    RiskRefusalReason,
)

__all__ = [
    "OrderRequest",
    "RiskContext",
    "RiskDecision",
    "RiskLimits",
    "RiskManager",
    "RiskRefusalReason",
    "limits",
]
