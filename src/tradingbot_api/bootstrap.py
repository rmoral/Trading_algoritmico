"""Bootstrap helpers run on API process startup.

`ensure_admin_user` creates the operator account from environment
variables on first run. Idempotent: if a user with the configured
username already exists, it is left untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingbot.logging_setup import get_logger
from tradingbot.persistence.models import User
from tradingbot.settings import Settings
from tradingbot_api.auth import hash_password


async def ensure_admin_user(db: AsyncSession, settings: Settings) -> User | None:
    """Create the admin user if missing. Return the user (or None if skipped)."""
    log = get_logger(__name__)
    username = settings.web_admin_username
    password = settings.web_admin_password.get_secret_value()

    if not password:
        log.warning(
            "admin_bootstrap_skipped_no_password",
            username=username,
            reason="WEB_ADMIN_PASSWORD is empty",
        )
        return None

    existing = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if existing is not None:
        log.info("admin_bootstrap_user_exists", username=username)
        return existing

    user = User(
        username=username,
        password_hash=hash_password(password),
        created_at=datetime.now(UTC),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    log.info("admin_bootstrap_user_created", username=username)
    return user
