"""`HaltStateStore`: shared trading-halt state for the risk manager.

The risk manager refuses entries while the active asset is halted and
during a short cooldown after it resumes (`halt_resume_cooldown_seconds`),
but only if `RiskContext.halt_active` / `halt_resumed_at` reflect
reality. `HaltMonitor` writes the live halt state here; the
risk-context builder reads it.

State lives in Redis, not bot memory, so a restart during a halt's
post-resume cooldown does not silently forget it. A short TTL keeps a
stale resume timestamp from outliving the cooldown it gates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

DEFAULT_KEY_PREFIX: str = "tradingbot:halt"
# An hour comfortably covers any LULD halt plus its cooldown, and lets
# stale state expire on its own after the session.
_TTL_SECONDS: int = 60 * 60


def _is_truthy(raw: Any) -> bool:
    if raw is None:
        return False
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    return text == "1"


def _parse_timestamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        return datetime.fromtimestamp(float(text), tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


class HaltStateStore:
    """Persist and report the active asset's trading-halt state."""

    def __init__(
        self, redis: Redis[Any], *, key_prefix: str = DEFAULT_KEY_PREFIX
    ) -> None:
        self._redis = redis
        self._active_key = f"{key_prefix}:active"
        self._resumed_key = f"{key_prefix}:resumed_at"

    async def update(self, halted: bool, *, now: datetime) -> None:
        """Record the current halt reading.

        A `halted -> not halted` transition stamps `resumed_at` with
        `now`, which gates the post-resume cooldown.
        """
        was_active = _is_truthy(await self._redis.get(self._active_key))
        if was_active and not halted:
            await self._redis.set(
                self._resumed_key, str(now.timestamp()), ex=_TTL_SECONDS
            )
        await self._redis.set(
            self._active_key, "1" if halted else "0", ex=_TTL_SECONDS
        )

    async def state(self) -> tuple[bool, datetime | None]:
        """Return `(halt_active, halt_resumed_at)` for the risk context."""
        active = _is_truthy(await self._redis.get(self._active_key))
        resumed_at = _parse_timestamp(await self._redis.get(self._resumed_key))
        return active, resumed_at


__all__ = ["HaltStateStore"]
