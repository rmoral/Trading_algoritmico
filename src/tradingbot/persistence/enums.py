"""Domain enumerations used by the ORM models.

All enums inherit from `StrEnum` so they serialize to a stable string
representation in both Python and the database. We use VARCHAR + CHECK
constraints rather than PG native enums to keep migrations cheap to
extend.
"""

from __future__ import annotations

from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    MARKET = "MARKET"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TimeInForce(StrEnum):
    DAY = "DAY"
    IOC = "IOC"
    GTC = "GTC"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class PositionState(StrEnum):
    ABRIENDO = "ABRIENDO"
    ABIERTA = "ABIERTA"
    CERRANDO = "CERRANDO"
    CERRADA = "CERRADA"


class SRKind(StrEnum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class BarResolution(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"


class RiskSeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
