"""Web API authentication helpers.

Three responsibilities:
- Hashing and verifying passwords with Argon2id (`argon2-cffi`).
- Creating, looking up, refreshing, and deleting session rows in the
  `app_sessions` table.
- FastAPI dependencies that pull the current user out of the request's
  cookies, raising 401 when missing or expired.

TOTP is not handled here yet; it will be added once the operator has
enrolled a second factor from the UI.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingbot.persistence.models import AppSession, User
from tradingbot.settings import Settings

if TYPE_CHECKING:
    pass

SESSION_COOKIE_NAME = "tradingbot_session"

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a password with Argon2id (default parameters)."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff `plain` matches `hashed`. Never raises on mismatch."""
    try:
        _hasher.verify(hashed, plain)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


async def create_session(
    db: AsyncSession,
    *,
    user: User,
    settings: Settings,
    ip_address: str | None = None,
) -> AppSession:
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.web_session_lifetime_days)
    session = AppSession(
        user_id=user.id,
        created_at=now,
        expires_at=expires_at,
        last_seen_at=now,
        ip_address=ip_address,
    )
    db.add(session)
    await db.flush()
    return session


async def fetch_active_session(db: AsyncSession, session_id: UUID) -> AppSession | None:
    now = datetime.now(UTC)
    result = await db.execute(
        select(AppSession).where(
            AppSession.id == session_id,
            AppSession.expires_at > now,
        )
    )
    return result.scalar_one_or_none()


async def delete_session(db: AsyncSession, session_id: UUID) -> None:
    session = await db.get(AppSession, session_id)
    if session is not None:
        await db.delete(session)


# =========================================================
# FastAPI dependencies
# =========================================================


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory_dep(
    request: Request,
) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_redis_dep(request: Request) -> object:
    """Return the shared `redis.asyncio.Redis` client.

    Typed as `object` to avoid a hard import of redis at this dep
    declaration point; routes annotate the concrete type.
    """
    return request.app.state.redis


async def get_current_user(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dep),
) -> User:
    """Resolve the operator from the session cookie.

    Raises 401 when:
    - cookie is missing,
    - cookie is not a valid UUID,
    - session does not exist or is expired,
    - the linked user is inactive.
    """
    if session_cookie is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        session_id = UUID(session_cookie)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session") from exc

    async with session_factory() as db:
        session = await fetch_active_session(db, session_id)
        if session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")
        user = await db.get(User, session.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user disabled")
        # Sliding refresh of last_seen_at.
        session.last_seen_at = datetime.now(UTC)
        await db.commit()
        return user
