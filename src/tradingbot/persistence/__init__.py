"""Persistence layer (CAPA 0).

Single source of truth for trading state. The bot and the web API both
import from this module; they never import from each other.
"""

from tradingbot.persistence.base import Base
from tradingbot.persistence.database import (
    create_engine,
    create_session_factory,
    get_engine,
    get_session_factory,
)

__all__ = [
    "Base",
    "create_engine",
    "create_session_factory",
    "get_engine",
    "get_session_factory",
]
