"""Config policy versioning + audit logging.

The runtime configuration lives in `config_policies` as an
append-only sequence of versions; the current one is the row with
`effective_to IS NULL`. Updates are atomic:

1. Close the prior current row (set its `effective_to` to now).
2. Insert the new row (`version = previous + 1`, `effective_to = NULL`).
3. Insert an `audit_log` entry capturing the actor + before/after payloads.

The partial unique index `ix_config_policies_single_current` is the
last line of defense against two writers landing two "current" rows
simultaneously; the loser receives an `IntegrityError`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingbot.persistence.models import AuditLog, ConfigPolicy
from tradingbot_api.config_schema import ConfigPolicyPayload


async def get_current_policy(db: AsyncSession) -> ConfigPolicy | None:
    """Return the single current policy, or None when uninitialized."""
    result = await db.execute(
        select(ConfigPolicy).where(ConfigPolicy.effective_to.is_(None))
    )
    return result.scalar_one_or_none()


async def update_policy(
    db: AsyncSession,
    *,
    payload: ConfigPolicyPayload,
    actor: str,
) -> ConfigPolicy:
    """Create a new policy version, retiring the previous one, and audit the change.

    All operations run inside the caller's session; the caller commits.
    """
    current = await get_current_policy(db)
    now = datetime.now(UTC)
    serialized = payload.model_dump(mode="json")

    new_version = (current.version + 1) if current is not None else 1
    new_id = uuid4()

    if current is not None:
        current.effective_to = now

    new_policy = ConfigPolicy(
        id=new_id,
        version=new_version,
        effective_from=now,
        effective_to=None,
        payload=serialized,
        created_by=actor,
        created_at=now,
    )
    db.add(new_policy)

    db.add(
        AuditLog(
            ts=now,
            actor=actor,
            action=(
                "config_policy_updated" if current is not None else "config_policy_created"
            ),
            entity_type="config_policy",
            entity_id=str(new_id),
            before=current.payload if current is not None else None,
            after=serialized,
        )
    )
    return new_policy
