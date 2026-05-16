"""Risk layer (CAPA 5).

In Phase 2 only the hardcoded safety caps live here. The runtime
`RiskManager.approve(order)` lands in Phase 3 alongside the strategy
engine.
"""

from tradingbot.risk import limits

__all__ = ["limits"]
