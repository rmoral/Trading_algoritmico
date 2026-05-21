"""Daily asset selection + audit log.

Same versioning pattern as `config_service`: the current selection is
the row with `effective_to IS NULL`. Updates retire the prior current
row, insert a new one, and audit the change.

Changing the asset while a position is open (state in ABRIENDO,
ABIERTA or CERRANDO) is refused at the service level — the operator
must close out first. Phase 3's bot logic will additionally enforce
this on its side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingbot.persistence.models import ActiveAssetSelection, AuditLog
from tradingbot.persistence.repositories import PositionsRepository


class AssetChangeBlockedError(Exception):
    """Raised when an open position prevents changing the active asset."""


async def get_current_asset(db: AsyncSession) -> ActiveAssetSelection | None:
    result = await db.execute(
        select(ActiveAssetSelection).where(ActiveAssetSelection.effective_to.is_(None))
    )
    return result.scalar_one_or_none()


async def set_active_asset(
    db: AsyncSession,
    *,
    symbol: str,
    actor: str,
    positions_repo: PositionsRepository,
    is_earnings_window: bool = False,
) -> ActiveAssetSelection:
    """Create a new selection, retiring the previous one. Caller commits.

    Refuses (raises AssetChangeBlockedError) when a position is open.
    `is_earnings_window` marks the asset as inside an earnings
    blackout, which the risk manager honours.
    """
    open_pos = await positions_repo.get_open_position()
    if open_pos is not None:
        raise AssetChangeBlockedError(
            f"cannot change active asset while position {open_pos.id} "
            f"({open_pos.symbol}, {open_pos.state}) is open"
        )

    now = datetime.now(UTC)
    current = await get_current_asset(db)
    before_symbol = current.symbol if current is not None else None

    if current is not None:
        current.effective_to = now

    new_id = uuid4()
    new = ActiveAssetSelection(
        id=new_id,
        symbol=symbol,
        effective_from=now,
        effective_to=None,
        set_by=actor,
        is_earnings_window=is_earnings_window,
    )
    db.add(new)

    db.add(
        AuditLog(
            ts=now,
            actor=actor,
            action=(
                "active_asset_changed"
                if current is not None
                else "active_asset_set"
            ),
            entity_type="active_asset_selection",
            entity_id=str(new_id),
            before={"symbol": before_symbol} if current is not None else None,
            after={"symbol": symbol, "is_earnings_window": is_earnings_window},
        )
    )
    return new
